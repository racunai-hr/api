"""JWT HR/payroll API views — thin wrappers around payroll.services.api."""

from __future__ import annotations

from django.http import Http404, HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.schema_common import ERROR_400, ERROR_401, ERROR_404, ERROR_409
from domains.hr.api.authentication import HrJWTAuthentication
from domains.hr.api.permissions import TenantHrReadPermission, TenantHrWritePermission
from domains.hr.api.schema import (
    ContractListSerializer,
    ContractSerializer,
    EmployeeListSerializer,
    EmployeeSerializer,
    PayrollPeriodListSerializer,
    PayrollPeriodSerializer,
    PayslipDetailSerializer,
)
from payroll.services.api import (
    PayrollApiError,
    calculate_period,
    create_contract,
    create_employee,
    create_period,
    add_payslip_adjustment,
    add_payslip_earning,
    generate_period,
    get_employee,
    get_payslip,
    get_payslip_for_pdf,
    get_period,
    get_period_for_pdf,
    list_contracts,
    list_employees,
    list_periods,
    list_worktime,
    lock_worktime,
    save_worktime_entry,
    post_period,
    update_contract,
    update_employee,
)
from payroll.services.pdf import (
    render_calculation_recap_pdf_bytes,
    render_payment_recap_pdf_bytes,
    render_payroll_journal_pdf_bytes,
    render_payslip_pdf_bytes,
)


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


def _error(exc: PayrollApiError):
    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class _HrApiView(APIView):
    authentication_classes = [HrJWTAuthentication]
    serializer_class = EmployeeSerializer

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


class _HrReadWriteApiView(_HrApiView):
    """GET uses read role; mutating methods use write role."""

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated(), TenantHrReadPermission()]
        return [IsAuthenticated(), TenantHrWritePermission()]


class _HrWriteApiView(_HrApiView):
    permission_classes = [IsAuthenticated, TenantHrWritePermission]


class _HrExportApiView(_HrApiView):
    permission_classes = [IsAuthenticated, TenantHrReadPermission]

    def perform_content_negotiation(self, request, force=False):
        renderer = self.renderer_classes[0]()
        return renderer, renderer.media_type


# --- Employees --------------------------------------------------------------


class EmployeeListView(_HrReadWriteApiView):
    @extend_schema(
        tags=['hr'],
        operation_id='hr_employees_list',
        responses={200: EmployeeListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request):
        return Response(list_employees(_require_tenant(request)))

    @extend_schema(
        tags=['hr'],
        operation_id='hr_employees_create',
        responses={201: EmployeeSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def post(self, request):
        try:
            payload = create_employee(_require_tenant(request), request.data)
        except PayrollApiError as exc:
            return _error(exc)
        return Response(payload, status=status.HTTP_201_CREATED)


class EmployeeDetailView(_HrReadWriteApiView):
    http_method_names = ['get', 'patch', 'head', 'options']

    @extend_schema(
        tags=['hr'],
        operation_id='hr_employees_retrieve',
        responses={200: EmployeeSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        payload = get_employee(_require_tenant(request), pk)
        if payload is None:
            raise Http404()
        return Response(payload)

    @extend_schema(
        tags=['hr'],
        operation_id='hr_employees_partial_update',
        responses={200: EmployeeSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def patch(self, request, pk: int):
        try:
            payload = update_employee(_require_tenant(request), pk, request.data)
        except PayrollApiError as exc:
            return _error(exc)
        if payload is None:
            raise Http404()
        return Response(payload)


class EmployeeContractListView(_HrReadWriteApiView):
    @extend_schema(
        tags=['hr'],
        operation_id='hr_employee_contracts_list',
        responses={200: ContractListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        payload = list_contracts(_require_tenant(request), pk)
        if payload is None:
            raise Http404()
        return Response(payload)

    @extend_schema(
        tags=['hr'],
        operation_id='hr_employee_contracts_create',
        responses={201: ContractSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def post(self, request, pk: int):
        try:
            payload = create_contract(_require_tenant(request), pk, request.data)
        except PayrollApiError as exc:
            return _error(exc)
        if payload is None:
            raise Http404()
        return Response(payload, status=status.HTTP_201_CREATED)


class EmployeeContractDetailView(_HrWriteApiView):
    http_method_names = ['patch', 'head', 'options']

    @extend_schema(
        tags=['hr'],
        operation_id='hr_employee_contracts_partial_update',
        responses={200: ContractSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def patch(self, request, pk: int, contract_id: int):
        try:
            payload = update_contract(_require_tenant(request), pk, contract_id, request.data)
        except PayrollApiError as exc:
            return _error(exc)
        if payload is None:
            raise Http404()
        return Response(payload)


# --- Payroll periods / payslips ---------------------------------------------


class PayrollPeriodListView(_HrReadWriteApiView):
    @extend_schema(
        tags=['hr'],
        operation_id='hr_payroll_periods_list',
        responses={200: PayrollPeriodListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request):
        return Response(list_periods(_require_tenant(request)))

    @extend_schema(
        tags=['hr'],
        operation_id='hr_payroll_periods_create',
        responses={201: PayrollPeriodSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def post(self, request):
        try:
            payload = create_period(_require_tenant(request), request.data)
        except PayrollApiError as exc:
            return _error(exc)
        return Response(payload, status=status.HTTP_201_CREATED)


class PayrollPeriodDetailView(_HrReadWriteApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['hr'],
        operation_id='hr_payroll_periods_retrieve',
        responses={200: PayrollPeriodSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        payload = get_period(_require_tenant(request), pk)
        if payload is None:
            raise Http404()
        return Response(payload)


class PayrollPeriodGenerateView(_HrWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['hr'],
        operation_id='hr_payroll_periods_generate',
        responses={200: PayrollPeriodSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def post(self, request, pk: int):
        payload = generate_period(_require_tenant(request), pk)
        if payload is None:
            raise Http404()
        return Response(payload)


class PayrollPeriodCalculateView(_HrWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['hr'],
        operation_id='hr_payroll_periods_calculate',
        responses={200: PayrollPeriodSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def post(self, request, pk: int):
        tenant = _require_tenant(request)
        try:
            payload = calculate_period(tenant, pk)
        except PayrollApiError as exc:
            return _error(exc)
        if payload is None:
            raise Http404()
        return Response(payload)


class PayrollPeriodPostView(_HrWriteApiView):
    http_method_names = ['post', 'options']

    @extend_schema(
        tags=['hr'],
        operation_id='hr_payroll_periods_post',
        responses={
            200: PayrollPeriodSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, pk: int):
        tenant = _require_tenant(request)
        try:
            payload = post_period(tenant, pk, request.user)
        except PayrollApiError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        if payload is None:
            raise Http404()
        return Response(payload)


def _pdf_response(pdf_bytes: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


class PayrollPeriodJournalPdfView(_HrExportApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(tags=['hr'], operation_id='hr_payroll_periods_journal_pdf')
    def get(self, request, pk: int):
        period = get_period_for_pdf(_require_tenant(request), pk)
        if period is None:
            raise Http404()
        pdf_bytes = render_payroll_journal_pdf_bytes(period)
        return _pdf_response(pdf_bytes, f'Knjizenje_place_{period.year}{period.month:02d}.pdf')


class PayrollPeriodPaymentRecapPdfView(_HrExportApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(tags=['hr'], operation_id='hr_payroll_periods_payment_recap_pdf')
    def get(self, request, pk: int):
        period = get_period_for_pdf(_require_tenant(request), pk)
        if period is None:
            raise Http404()
        pdf_bytes = render_payment_recap_pdf_bytes(period)
        return _pdf_response(pdf_bytes, f'Rekapitulacija_isplata_{period.year}{period.month:02d}.pdf')


class PayrollPeriodCalculationRecapPdfView(_HrExportApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(tags=['hr'], operation_id='hr_payroll_periods_calculation_recap_pdf')
    def get(self, request, pk: int):
        period = get_period_for_pdf(_require_tenant(request), pk)
        if period is None:
            raise Http404()
        pdf_bytes = render_calculation_recap_pdf_bytes(period)
        return _pdf_response(pdf_bytes, f'Rekapitulacija_obracuna_{period.year}{period.month:02d}.pdf')


class PayslipDetailView(_HrReadWriteApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(
        tags=['hr'],
        operation_id='hr_payslips_retrieve',
        responses={200: PayslipDetailSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        payload = get_payslip(_require_tenant(request), pk)
        if payload is None:
            raise Http404()
        return Response(payload)


class PayslipPdfView(_HrExportApiView):
    http_method_names = ['get', 'head', 'options']

    @extend_schema(tags=['hr'], operation_id='hr_payslips_pdf')
    def get(self, request, pk: int):
        payslip = get_payslip_for_pdf(_require_tenant(request), pk)
        if payslip is None:
            raise Http404()
        pdf_bytes = render_payslip_pdf_bytes(payslip)
        return _pdf_response(pdf_bytes, f'Obracunska_lista_{payslip.employee.last_name}_{payslip.id}.pdf')


class WorkTimeListView(_HrReadWriteApiView):
    @extend_schema(tags=['hr'], operation_id='hr_worktime_list', responses={200: EmployeeSerializer, 401: ERROR_401, 404: ERROR_404})
    def get(self, request):
        try:
            employee_id = int(request.query_params['employee'])
            year = int(request.query_params['year'])
            month = int(request.query_params['month'])
        except (KeyError, TypeError, ValueError):
            return Response({'detail': 'employee, year i month su obavezni.'}, status=status.HTTP_400_BAD_REQUEST)
        payload = list_worktime(_require_tenant(request), employee_id, year, month)
        if payload is None:
            raise Http404()
        return Response(payload)

    @extend_schema(tags=['hr'], operation_id='hr_worktime_create', responses={201: EmployeeSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404})
    def post(self, request):
        try:
            payload = save_worktime_entry(_require_tenant(request), request.data, request.user)
        except PayrollApiError as exc:
            return _error(exc)
        return Response(payload, status=status.HTTP_201_CREATED)


class WorkTimeLockView(_HrWriteApiView):
    @extend_schema(tags=['hr'], operation_id='hr_worktime_lock', responses={200: EmployeeSerializer, 401: ERROR_401, 404: ERROR_404})
    def post(self, request):
        try:
            employee_id = int(request.data['employee_id'])
            year = int(request.data['year'])
            month = int(request.data['month'])
        except (KeyError, TypeError, ValueError):
            return Response({'detail': 'employee_id, year i month su obavezni.'}, status=status.HTTP_400_BAD_REQUEST)
        payload = lock_worktime(_require_tenant(request), employee_id, year, month, request.user)
        if payload is None:
            raise Http404()
        return Response(payload)


class PayslipEarningListView(_HrWriteApiView):
    @extend_schema(tags=['hr'], operation_id='hr_payslip_earnings_create', responses={201: PayslipDetailSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404})
    def post(self, request, pk: int):
        try:
            payload = add_payslip_earning(_require_tenant(request), pk, request.data)
        except PayrollApiError as exc:
            return _error(exc)
        if payload is None:
            raise Http404()
        return Response(payload, status=status.HTTP_201_CREATED)


class PayslipAdjustmentListView(_HrWriteApiView):
    @extend_schema(tags=['hr'], operation_id='hr_payslip_adjustments_create', responses={201: PayslipDetailSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404})
    def post(self, request, pk: int):
        try:
            payload = add_payslip_adjustment(_require_tenant(request), pk, request.data)
        except PayrollApiError as exc:
            return _error(exc)
        if payload is None:
            raise Http404()
        return Response(payload, status=status.HTTP_201_CREATED)
