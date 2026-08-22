"""JWT purchasing OCR API views."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.schema_common import ERROR_400, ERROR_401, ERROR_404, ERROR_409, ERROR_422
from domains.purchasing.api.authentication import PurchasingJWTAuthentication
from domains.purchasing.api.permissions import TenantPurchasingWritePermission
from domains.purchasing.api.schema import (
    ConfirmInvoiceImportSerializer,
    CreatePartnerFromImportSerializer,
    EracunRejectionRequestSerializer,
    EracunRejectionResponseSerializer,
    IncomingInvoiceImportSerializer,
    PurchasingConflictSerializer,
)
from domains.purchasing.services.confirm import confirm_invoice_import
from domains.purchasing.services.exceptions import PurchasingBadRequest, PurchasingConflict
from domains.purchasing.services.invoice_import import (
    apply_partner_updates,
    create_partner_from_import,
    discard_invoice_import,
    get_invoice_import,
    retry_invoice_import,
    submit_invoice_import,
)
from expenses.models import Expense
from integrations.services.inbound_rejection import InboundRejectionError, submit_eracun_rejection


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


def _error_detail(exc: DjangoValidationError):
    if hasattr(exc, 'message_dict') and exc.message_dict:
        return exc.message_dict
    if hasattr(exc, 'messages'):
        messages = list(exc.messages)
        return messages[0] if len(messages) == 1 else messages
    return str(exc)


def _conflict(exc: PurchasingConflict):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_409_CONFLICT)


def _bad_request(exc: PurchasingBadRequest):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)


class _PurchasingApiView(APIView):
    authentication_classes = [PurchasingJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantPurchasingWritePermission]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


class InvoiceImportCreateView(_PurchasingApiView):
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=['purchasing'],
        operation_id='purchasing_invoice_imports_create',
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
            ),
        ],
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {'file': {'type': 'string', 'format': 'binary'}},
                'required': ['file'],
            }
        },
        responses={
            202: IncomingInvoiceImportSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: PurchasingConflictSerializer,
            422: ERROR_422,
        },
    )
    def post(self, request):
        tenant = _require_tenant(request)
        upload = request.FILES.get('file')
        if upload is None:
            return Response(
                {'detail': {'file': 'Polje file je obavezno.'}},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        try:
            content = upload.read()
            payload, code, _created = submit_invoice_import(
                tenant=tenant,
                actor=request.user,
                content=content,
                filename=getattr(upload, 'name', '') or '',
                idempotency_key=request.headers.get('Idempotency-Key', ''),
            )
        except PurchasingConflict as exc:
            return _conflict(exc)
        except DjangoValidationError as exc:
            return Response({'detail': _error_detail(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return Response(payload, status=code)


class InvoiceImportDetailView(_PurchasingApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['purchasing'],
        operation_id='purchasing_invoice_imports_retrieve',
        responses={200: IncomingInvoiceImportSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        return Response(get_invoice_import(tenant=_require_tenant(request), import_id=pk))


class InvoiceImportRetryView(_PurchasingApiView):
    @extend_schema(
        tags=['purchasing'],
        operation_id='purchasing_invoice_imports_retry',
        request=None,
        responses={
            200: IncomingInvoiceImportSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: PurchasingConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(retry_invoice_import(tenant=_require_tenant(request), import_id=pk))
        except PurchasingConflict as exc:
            return _conflict(exc)


class InvoiceImportCreatePartnerView(_PurchasingApiView):
    @extend_schema(
        tags=['purchasing'],
        operation_id='purchasing_invoice_imports_create_partner',
        request=CreatePartnerFromImportSerializer,
        responses={
            200: IncomingInvoiceImportSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: PurchasingConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(
                create_partner_from_import(
                    tenant=_require_tenant(request),
                    import_id=pk,
                    data=request.data,
                    user=request.user,
                )
            )
        except PurchasingConflict as exc:
            return _conflict(exc)
        except PurchasingBadRequest as exc:
            return _bad_request(exc)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc


class InvoiceImportApplyPartnerUpdatesView(_PurchasingApiView):
    @extend_schema(
        tags=['purchasing'],
        operation_id='purchasing_invoice_imports_apply_partner_updates',
        request=None,
        responses={
            200: IncomingInvoiceImportSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: PurchasingConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(apply_partner_updates(tenant=_require_tenant(request), import_id=pk))
        except PurchasingConflict as exc:
            return _conflict(exc)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc


class InvoiceImportConfirmView(_PurchasingApiView):
    @extend_schema(
        tags=['purchasing'],
        operation_id='purchasing_invoice_imports_confirm',
        request=ConfirmInvoiceImportSerializer,
        responses={
            200: IncomingInvoiceImportSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: PurchasingConflictSerializer,
            422: ERROR_422,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(
                confirm_invoice_import(
                    tenant=_require_tenant(request),
                    import_id=pk,
                    actor=request.user,
                    data=request.data,
                )
            )
        except PurchasingConflict as exc:
            return _conflict(exc)
        except DjangoValidationError as exc:
            return Response({'detail': _error_detail(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)


class InvoiceImportDiscardView(_PurchasingApiView):
    @extend_schema(
        tags=['purchasing'],
        operation_id='purchasing_invoice_imports_discard',
        request=None,
        responses={
            200: IncomingInvoiceImportSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: PurchasingConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(discard_invoice_import(tenant=_require_tenant(request), import_id=pk))
        except PurchasingConflict as exc:
            return _conflict(exc)


def _require_idempotency_key(request) -> str:
    key = (request.headers.get('Idempotency-Key') or request.META.get('HTTP_IDEMPOTENCY_KEY') or '').strip()
    if not key:
        raise ValidationError({'Idempotency-Key': 'Header Idempotency-Key je obavezan.'})
    return key


class ExpenseEracunRejectionView(_PurchasingApiView):
    @extend_schema(
        tags=['purchasing'],
        operation_id='purchasing_expenses_eracun_rejection',
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
            ),
        ],
        request=EracunRejectionRequestSerializer,
        responses={
            202: EracunRejectionResponseSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: PurchasingConflictSerializer,
            502: PurchasingConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        tenant = _require_tenant(request)
        expense = Expense.all_objects.filter(tenant=tenant, pk=pk).first()
        if expense is None:
            raise Http404()
        serializer = EracunRejectionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            idempotency_key = _require_idempotency_key(request)
            body = submit_eracun_rejection(
                expense,
                reason_code=serializer.validated_data['reason_code'],
                reason_text=serializer.validated_data.get('reason_text') or '',
                idempotency_key=idempotency_key,
            )
        except InboundRejectionError as exc:
            status_code = (
                status.HTTP_502_BAD_GATEWAY
                if exc.http_status == 502
                else (
                    status.HTTP_400_BAD_REQUEST
                    if exc.http_status == 400
                    else status.HTTP_409_CONFLICT
                )
            )
            return Response({'code': exc.code, 'detail': exc.detail}, status=status_code)
        return Response(body, status=status.HTTP_202_ACCEPTED)
