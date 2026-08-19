from __future__ import annotations

from django.http import FileResponse, Http404, HttpResponse
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from domains.reporting.api.authentication import DocumentJWTAuthentication
from domains.reporting.api.permissions import TenantDocumentReadPermission
from domains.reporting.documents.attachments import (
    ATTACHMENT_CONTENT_UNAVAILABLE,
    is_missing_storage_error,
    safe_download_filename,
)
from domains.reporting.documents.filters import parse_filters
from domains.reporting.documents.service import (
    ExportLimitExceeded,
    export_documents,
    get_document_detail,
    get_expense_attachment,
    list_documents,
)
from domains.reporting.documents.pdf import (
    INVOICE_PDF_UNAVAILABLE,
    InvoicePdfUnavailable,
    outgoing_pdf_filename,
    render_outgoing_invoice_pdf,
)
from invoices.models import Invoice


class _DocumentApiView(APIView):
    authentication_classes = [DocumentJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantDocumentReadPermission]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


class DocumentListView(_DocumentApiView):
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_filters(request.query_params)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_documents(tenant, filters))


class DocumentDetailView(_DocumentApiView):
    def get(self, request, direction, pk):
        tenant = _require_tenant(request)
        if direction not in ('outgoing', 'incoming'):
            raise Http404()
        return Response(get_document_detail(tenant, direction, pk))


class DocumentExportView(_DocumentApiView):
    def perform_content_negotiation(self, request, force=False):
        """`?format=` is the CSV/XLSX file type, not a DRF renderer suffix."""
        renderer = self.renderer_classes[0]()
        return renderer, renderer.media_type

    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_filters(request.query_params)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        fmt = (request.query_params.get('format') or 'csv').lower()
        if fmt not in ('csv', 'xlsx'):
            raise ValidationError({'format': 'format mora biti csv ili xlsx'})
        try:
            body, content_type, filename = export_documents(tenant, filters, fmt)
        except ExportLimitExceeded as exc:
            return Response(
                {'detail': str(exc), 'limit': exc.limit, 'count': exc.count},
                status=400,
            )
        response = HttpResponse(body, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class DocumentPdfView(_DocumentApiView):
    def get(self, request, direction, pk):
        tenant = _require_tenant(request)
        if direction != 'outgoing':
            raise Http404()
        invoice = (
            Invoice.all_objects.filter(tenant=tenant, pk=pk)
            .select_related('responsible_person', 'company_to')
            .prefetch_related('items')
            .first()
        )
        if invoice is None:
            raise Http404()
        try:
            pdf_bytes = render_outgoing_invoice_pdf(request, invoice)
        except InvoicePdfUnavailable:
            return Response(INVOICE_PDF_UNAVAILABLE, status=503)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        filename = outgoing_pdf_filename(invoice)
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response


class DocumentAttachmentDownloadView(_DocumentApiView):
    def get(self, request, pk, attachment_id):
        tenant = _require_tenant(request)
        attachment = get_expense_attachment(tenant, pk, attachment_id)
        if not attachment.file:
            return Response(ATTACHMENT_CONTENT_UNAVAILABLE, status=410)
        filename = safe_download_filename(attachment.original_filename)
        try:
            handle = attachment.file.open('rb')
        except OSError as exc:
            if is_missing_storage_error(exc):
                return Response(ATTACHMENT_CONTENT_UNAVAILABLE, status=410)
            raise
        return FileResponse(handle, as_attachment=False, filename=filename)
