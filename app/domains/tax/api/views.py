"""Tax JWT API — PDV / PDV-S period workflow (locked endpoint map)."""

from __future__ import annotations

from uuid import UUID

from django.http import Http404, HttpResponse
from django.utils.dateparse import parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.schema_common import ERROR_400, ERROR_401, ERROR_404, ERROR_409
from domains.tax.api.authentication import TaxJWTAuthentication
from domains.tax.api.permissions import (
    TenantTaxExportPermission,
    TenantTaxReadPermission,
    TenantTaxWritePermission,
)
from domains.tax.api.schema import (
    ConfirmationResultSerializer,
    ConfirmationUploadSerializer,
    JoppdDraftSerializer,
    JoppdPeriodSerializer,
    JoppdSubmitRequestSerializer,
    PdvBoxesSerializer,
    PdvDraftSerializer,
    PdvLedgerRebuildSerializer,
    PdvPeriodListSerializer,
    PdvPeriodWorkspaceSerializer,
    PdvSPeriodSerializer,
    PdvSSubmitRequestSerializer,
    PdvSubmitRequestSerializer,
    SubmissionResultSerializer,
    PdYearSerializer,
    Tz2SaveRequestSerializer,
    Tz2YearSerializer,
)
from domains.tax.read.joppd import joppd_period_dto
from domains.tax.read.service import (
    get_vat_period,
    list_pdv_periods,
    pdv_boxes_dto,
    pdv_s_period_dto,
    zp_period_dto,
    pdv_workspace_dto,
)
from domains.tax.write.service import (
    TaxBadRequest,
    TaxConflict,
    TaxNotFound,
    attach_period_confirmation,
    confirmation_download,
    create_joppd_draft,
    create_period_draft,
    joppd_xml_bytes,
    pdv_s_xml_bytes,
    pdv_unsigned_xml_bytes,
    prepare_period_correction,
    rebuild_period_ledger,
    submit_joppd_period,
    submit_pdv_period,
    book_pdv_in_processing,
    submit_pdv_s_period,
    submit_zp_period,
    zp_xml_bytes,
)
from domains.tax.read.pd import pd_year_dto
from domains.tax.read.tz2 import tz2_year_dto
from domains.tax.write.tz2 import parse_tax_year, save_tz2_year, submit_tz2_year, tz2_xml_bytes


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


def _period_or_404(tenant, period: str):
    vat_period = get_vat_period(tenant, period)
    if vat_period is None:
        raise Http404()
    return vat_period


def _joppd_period_or_404(tenant, period: str) -> tuple[int, int]:
    """Parse a YYYY-MM period key and verify a payroll period exists for it.

    JOPPD is not tied to VATPeriod — the backing record is ``payroll.PayrollPeriod``,
    looked up only through ``domains.hr.facade`` (DTO-only, per domain dependency rule).
    """
    from domains.tax.read.dto import parse_period_key

    parsed = parse_period_key(period)
    if parsed is None:
        raise Http404()
    year, month = parsed
    from domains.hr.facade import get_payroll_period_summary

    if get_payroll_period_summary(tenant, year, month) is None:
        raise Http404()
    return year, month


def _error_response(exc, http_status):
    return Response({'detail': str(exc.detail if hasattr(exc, 'detail') else exc)}, status=http_status)


class TaxApiView(APIView):
    authentication_classes = [TaxJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantTaxReadPermission]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message, code=code)


class TaxWriteApiView(TaxApiView):
    permission_classes = [IsAuthenticated, TenantTaxWritePermission]


class TaxExportApiView(TaxApiView):
    permission_classes = [IsAuthenticated, TenantTaxExportPermission]

    def perform_content_negotiation(self, request, force=False):
        renderer = self.renderer_classes[0]()
        return renderer, renderer.media_type


class PdvPeriodListView(TaxApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_periods_list',
        responses={
            200: PdvPeriodListSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request):
        return Response(list_pdv_periods(_require_tenant(request)))


class PdvPeriodWorkspaceView(TaxApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_period_retrieve',
        responses={
            200: PdvPeriodWorkspaceSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        return Response(pdv_workspace_dto(vat_period))


class PdvPeriodLedgerView(TaxWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_period_ledger',
        request=None,
        responses={
            200: PdvLedgerRebuildSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, period):
        tenant = _require_tenant(request)
        try:
            payload = rebuild_period_ledger(tenant, period, actor=request.user)
        except TaxNotFound:
            raise Http404() from None
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        return Response(payload)


class PdvPeriodBoxesView(TaxApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_period_boxes',
        responses={
            200: PdvBoxesSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            return Response(pdv_boxes_dto(vat_period))
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class PdvPeriodDraftView(TaxWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_period_draft',
        request=None,
        responses={
            201: PdvDraftSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            payload = create_period_draft(vat_period)
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        return Response(payload, status=status.HTTP_201_CREATED)


class PdvPeriodCorrectionView(TaxWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_period_correction',
        request=None,
        responses={
            201: PdvDraftSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            payload = prepare_period_correction(vat_period, actor=request.user)
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        return Response(payload, status=status.HTTP_201_CREATED)


class PdvPeriodXmlView(TaxExportApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_period_xml',
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description='Unsigned Obrazac PDV XML'),
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def get(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            xml_bytes, filename = pdv_unsigned_xml_bytes(vat_period)
        except TaxNotFound:
            raise Http404() from None
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        response = HttpResponse(xml_bytes, content_type='application/xml')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


def _parse_submit_common(data) -> tuple[UUID, object]:
    identifier, submitted_at = _parse_submit_optional(data)
    if identifier is None or submitted_at is None:
        raise TaxBadRequest('eporezna_identifier i submitted_at su obavezni.')
    return identifier, submitted_at


def _parse_submit_optional(data) -> tuple[UUID | None, object | None]:
    raw_id = data.get('eporezna_identifier')
    raw_at = data.get('submitted_at')
    identifier = None
    submitted_at = None
    if raw_id:
        try:
            identifier = UUID(str(raw_id))
        except ValueError as exc:
            raise TaxBadRequest('eporezna_identifier mora biti UUID.') from exc
    if raw_at:
        submitted_at = parse_datetime(str(raw_at))
        if submitted_at is None:
            raise TaxBadRequest('submitted_at mora biti ISO datetime.')
    return identifier, submitted_at


class PdvPeriodSubmitView(TaxWriteApiView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_period_submit',
        request=PdvSubmitRequestSerializer,
        responses={
            200: SubmissionResultSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            identifier, submitted_at = _parse_submit_optional(request.data)
            raw_version = request.data.get('return_version')
            if raw_version is None:
                raise TaxBadRequest('return_version je obavezan.')
            return_version = int(raw_version)
            payload = submit_pdv_period(
                vat_period,
                user=request.user,
                eporezna_identifier=identifier,
                submitted_at=submitted_at,
                return_version=return_version,
                submitted_xml=request.FILES.get('submitted_xml'),
            )
        except TaxNotFound:
            raise Http404() from None
        except (TaxBadRequest, ValueError, TypeError) as exc:
            detail = getattr(exc, 'detail', str(exc))
            return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        return Response(payload)


class PdvPeriodProcessedView(TaxWriteApiView):
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_period_processed',
        request=PdvSubmitRequestSerializer,
        responses={
            200: SubmissionResultSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            raw_version = request.data.get('return_version')
            if raw_version is None:
                raise TaxBadRequest('return_version je obavezan.')
            payload = book_pdv_in_processing(
                vat_period,
                user=request.user,
                return_version=int(raw_version),
                submitted_xml=request.FILES.get('submitted_xml'),
            )
        except TaxNotFound:
            raise Http404() from None
        except (TaxBadRequest, ValueError, TypeError) as exc:
            detail = getattr(exc, 'detail', str(exc))
            return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        return Response(payload)


class PdvSPeriodView(TaxApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_s_period_retrieve',
        responses={
            200: PdvSPeriodSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            return Response(pdv_s_period_dto(vat_period))
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class PdvSPeriodXmlView(TaxExportApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_s_period_xml',
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description='Unsigned Obrazac PDV-S XML'),
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            xml_bytes, filename = pdv_s_xml_bytes(vat_period)
        except TaxNotFound:
            raise Http404() from None
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        response = HttpResponse(xml_bytes, content_type='application/xml')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class PdvSPeriodSubmitView(TaxWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_s_period_submit',
        request=PdvSSubmitRequestSerializer,
        responses={
            200: SubmissionResultSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            identifier, submitted_at = _parse_submit_common(request.data)
            payload = submit_pdv_s_period(
                vat_period,
                user=request.user,
                eporezna_identifier=identifier,
                submitted_at=submitted_at,
            )
        except TaxNotFound:
            raise Http404() from None
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        return Response(payload)


class PdvIspravakXmlView(TaxWriteApiView):
    serializer_class = PdvDraftSerializer
    http_method_names = ['post', 'options']

    def perform_content_negotiation(self, request, force=False):
        renderer = self.renderer_classes[0]()
        return renderer, renderer.media_type

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_ispravak_xml',
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description='Unsigned Obrazac PDV-ispravak XML'),
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def post(self, request):
        from accounting.services.tax_forms.pdv_ispravak.build import (
            PdvIspravakBuildError,
            pdv_ispravak_xml_bytes,
        )
        from domains.tax.read.dto import parse_period_key

        tenant = _require_tenant(request)
        parsed = parse_period_key(str(request.data.get('period') or ''))
        if parsed is None:
            return Response({'detail': 'Razdoblje mora biti YYYY-MM.'}, status=status.HTTP_400_BAD_REQUEST)
        year, month = parsed
        rows = request.data.get('rows')
        if not isinstance(rows, list):
            return Response({'detail': 'Retci računa su obavezni.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            xml_bytes, filename = pdv_ispravak_xml_bytes(tenant, year=year, month=month, rows=rows)
        except PdvIspravakBuildError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        response = HttpResponse(xml_bytes, content_type='application/xml')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class ZpPeriodView(TaxApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_zp_period_retrieve',
        responses={
            200: PdvSPeriodSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            return Response(zp_period_dto(vat_period))
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ZpPeriodXmlView(TaxExportApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_zp_period_xml',
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description='Unsigned Obrazac ZP XML'),
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            xml_bytes, filename = zp_xml_bytes(vat_period)
        except TaxNotFound:
            raise Http404() from None
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        response = HttpResponse(xml_bytes, content_type='application/xml')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class ZpPeriodSubmitView(TaxWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_zp_period_submit',
        request=PdvSSubmitRequestSerializer,
        responses={
            200: SubmissionResultSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            identifier, submitted_at = _parse_submit_common(request.data)
            payload = submit_zp_period(
                vat_period,
                user=request.user,
                eporezna_identifier=identifier,
                submitted_at=submitted_at,
            )
        except TaxNotFound:
            raise Http404() from None
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        return Response(payload)


class JoppdPeriodView(TaxApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_joppd_period_retrieve',
        responses={
            200: JoppdPeriodSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, period):
        year, month = _joppd_period_or_404(_require_tenant(request), period)
        try:
            return Response(joppd_period_dto(_require_tenant(request), year, month))
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class JoppdPeriodDraftView(TaxWriteApiView):
    serializer_class = JoppdDraftSerializer
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_joppd_period_draft',
        responses={
            200: JoppdDraftSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def post(self, request, period):
        tenant = _require_tenant(request)
        year, month = _joppd_period_or_404(tenant, period)
        try:
            payload = create_joppd_draft(tenant, year, month)
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class JoppdPeriodXmlView(TaxExportApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_joppd_period_xml',
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description='Unsigned draft Obrazac JOPPD XML'),
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, period):
        tenant = _require_tenant(request)
        year, month = _joppd_period_or_404(tenant, period)
        try:
            xml_bytes, filename = joppd_xml_bytes(tenant, year, month)
        except TaxNotFound:
            raise Http404() from None
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        response = HttpResponse(xml_bytes, content_type='application/xml')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class JoppdPeriodSubmitView(TaxWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_joppd_period_submit',
        request=JoppdSubmitRequestSerializer,
        responses={
            200: SubmissionResultSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, period):
        tenant = _require_tenant(request)
        year, month = _joppd_period_or_404(tenant, period)
        try:
            identifier, submitted_at = _parse_submit_common(request.data)
            payload = submit_joppd_period(
                tenant,
                year,
                month,
                user=request.user,
                eporezna_identifier=identifier,
                submitted_at=submitted_at,
            )
        except TaxNotFound:
            raise Http404() from None
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        return Response(payload)


class SubmissionConfirmationView(TaxApiView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ['get', 'post', 'options']

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), TenantTaxWritePermission()]
        return [IsAuthenticated(), TenantTaxReadPermission()]

    @extend_schema(
        tags=['tax'],
        operation_id='tax_submission_confirmation_download',
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description='Potvrda zaprimanja'),
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, event_uuid):
        try:
            data, filename = confirmation_download(_require_tenant(request), event_uuid)
        except TaxNotFound:
            raise Http404() from None
        response = HttpResponse(data, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    @extend_schema(
        tags=['tax'],
        operation_id='tax_submission_confirmation',
        request=ConfirmationUploadSerializer,
        responses={
            200: ConfirmationResultSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, event_uuid):
        tenant = _require_tenant(request)
        file_obj = request.FILES.get('confirmation')
        try:
            payload = attach_period_confirmation(
                tenant,
                event_uuid,
                file_obj,
                uploaded_by=request.user,
            )
        except TaxNotFound:
            raise Http404() from None
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        return Response(payload)


class Tz2YearView(TaxApiView):
    http_method_names = ['get', 'put', 'head', 'options']

    def get_permissions(self):
        if self.request.method == 'PUT':
            return [IsAuthenticated(), TenantTaxWritePermission()]
        return super().get_permissions()

    @extend_schema(
        tags=['tax'],
        operation_id='tax_tz2_year_retrieve',
        responses={200: Tz2YearSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, year):
        tenant = _require_tenant(request)
        tax_year = parse_tax_year(year)
        return Response(tz2_year_dto(tenant, tax_year))

    @extend_schema(
        tags=['tax'],
        operation_id='tax_tz2_year_save',
        request=Tz2SaveRequestSerializer,
        responses={
            200: Tz2YearSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def put(self, request, year):
        tenant = _require_tenant(request)
        tax_year = parse_tax_year(year)
        try:
            save_tz2_year(tenant, tax_year, request.data)
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        return Response(tz2_year_dto(tenant, tax_year))


class PdYearView(TaxApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pd_year_retrieve',
        responses={200: PdYearSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, year):
        tenant = _require_tenant(request)
        try:
            tax_year = parse_tax_year(year)
        except TaxNotFound:
            raise Http404() from None
        return Response(pd_year_dto(tenant, tax_year))


class Tz2YearXmlView(TaxExportApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_tz2_year_xml',
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description='Unsigned Obrazac TZ 2 XML'),
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, year):
        tenant = _require_tenant(request)
        tax_year = parse_tax_year(year)
        try:
            xml_bytes, filename = tz2_xml_bytes(tenant, tax_year)
        except TaxNotFound:
            raise Http404() from None
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        response = HttpResponse(xml_bytes, content_type='application/xml')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class Tz2YearSubmitView(TaxWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['tax'],
        operation_id='tax_tz2_year_submit',
        request=PdvSSubmitRequestSerializer,
        responses={
            200: SubmissionResultSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, year):
        tenant = _require_tenant(request)
        tax_year = parse_tax_year(year)
        try:
            identifier, submitted_at = _parse_submit_common(request.data)
            payload = submit_tz2_year(
                tenant,
                tax_year,
                user=request.user,
                eporezna_identifier=identifier,
                submitted_at=submitted_at,
            )
        except TaxNotFound:
            raise Http404() from None
        except TaxBadRequest as exc:
            return _error_response(exc, status.HTTP_400_BAD_REQUEST)
        except TaxConflict as exc:
            return _error_response(exc, status.HTTP_409_CONFLICT)
        return Response(payload)
