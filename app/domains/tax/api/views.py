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
    PdvBoxesSerializer,
    PdvDraftSerializer,
    PdvLedgerRebuildSerializer,
    PdvPeriodListSerializer,
    PdvPeriodWorkspaceSerializer,
    PdvSPeriodSerializer,
    PdvSSubmitRequestSerializer,
    PdvSubmitRequestSerializer,
    SubmissionResultSerializer,
)
from domains.tax.read.service import (
    get_vat_period,
    list_pdv_periods,
    pdv_boxes_dto,
    pdv_s_period_dto,
    pdv_workspace_dto,
)
from domains.tax.write.service import (
    TaxBadRequest,
    TaxConflict,
    TaxNotFound,
    attach_period_confirmation,
    create_period_draft,
    pdv_s_xml_bytes,
    pdv_unsigned_xml_bytes,
    rebuild_period_ledger,
    submit_pdv_period,
    submit_pdv_s_period,
)


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
        },
    )
    def post(self, request, period):
        vat_period = _period_or_404(_require_tenant(request), period)
        try:
            payload = create_period_draft(vat_period)
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
    raw_id = data.get('eporezna_identifier')
    raw_at = data.get('submitted_at')
    if not raw_id or not raw_at:
        raise TaxBadRequest('eporezna_identifier i submitted_at su obavezni.')
    try:
        identifier = UUID(str(raw_id))
    except ValueError as exc:
        raise TaxBadRequest('eporezna_identifier mora biti UUID.') from exc
    submitted_at = parse_datetime(str(raw_at))
    if submitted_at is None:
        raise TaxBadRequest('submitted_at mora biti ISO datetime.')
    return identifier, submitted_at


class PdvPeriodSubmitView(TaxWriteApiView):
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
            identifier, submitted_at = _parse_submit_common(request.data)
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


class SubmissionConfirmationView(TaxWriteApiView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ['post', 'options']

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
