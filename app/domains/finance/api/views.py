"""Finance partner read API + Deposit write API (ADR-0022 / ADR-0024)."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.schema_common import ERROR_400, ERROR_401, ERROR_404, ERROR_409
from domains.finance.api.authentication import FinanceJWTAuthentication
from domains.finance.api.permissions import TenantFinanceReadPermission, TenantFinanceWritePermission
from domains.finance.api.schema import (
    JOURNAL_ENTRY_LIST_PARAMS,
    PARTNER_STATEMENT_PARAMS,
    PARTNER_SUBLEDGER_PARAMS,
    CHART_OF_ACCOUNTS_PARAMS,
    ChartOfAccountsListSerializer,
    CreateDepositSerializer,
    CreateOfficialDocumentSerializer,
    CreatePrivateFundsClaimSerializer,
    DepositConflictSerializer,
    DepositListSerializer,
    DepositSerializer,
    ExpenseApproveResponseSerializer,
    CostCenterListSerializer,
    CostCenterPatchSerializer,
    CostCenterReportSerializer,
    CostCenterSerializer,
    CostCenterWriteSerializer,
    COST_CENTER_REPORT_PARAMS,
    LinkOfficialDocumentJournalSerializer,
    OfficialDocumentPostingProfileSerializer,
    OfficialDocumentSerializer,
    SetOfficialDocumentPostingProfileSerializer,
    ExpenseDraftPatchSerializer,
    ExpensePostingPreviewSerializer,
    JournalEntryDetailSerializer,
    PaginatedJournalEntriesSerializer,
    PartnerFinancialSummarySerializer,
    PartnerStatementSerializer,
    PartnerSubledgerListSerializer,
    PrivateFundsClaimSerializer,
    ReturnDepositSerializer,
)
from domains.finance.read.filters import parse_journal_entry_filters
from domains.finance.read.service import get_journal_entry, list_journal_entries
from domains.finance.services.aging import partner_financial_summary, partner_subledger_items
from domains.finance.services.statement import partner_statement
from domains.finance.services.deposits import (
    DepositBadRequest,
    DepositConflict,
    cancel_deposit,
    create_deposit,
    get_deposit,
    list_deposits,
    post_deposit,
    return_deposit,
    reverse_deposit,
)
from domains.finance.services.expenses import (
    ExpenseApproveBadRequest,
    ExpenseApproveConflict,
    approve_expense_for_posting,
    posting_preview,
    update_draft_expense_posting,
)
from domains.finance.services.chart_accounts import list_postable_accounts
from domains.finance.services.cost_centers import (
    create_cost_center,
    list_cost_centers,
    update_cost_center,
)
from accounting.services.reports import cost_center_report
from domains.finance.services.account_resolver import ExpenseAccountResolutionError
from domains.finance.services.official_documents import (
    OfficialDocumentBadRequest,
    OfficialDocumentConflict,
    cancel_official_document,
    create_official_document,
    get_official_document,
    link_official_document_journal,
    list_official_document_posting_profiles,
    post_official_document,
    register_official_document,
    set_official_document_posting_profile,
)
from domains.finance.services.private_funds import (
    PrivateFundsBadRequest,
    PrivateFundsConflict,
    create_claim,
    get_claim,
    post_claim,
)
from partners.models import Partner


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


def _require_partner(tenant, partner_id: int) -> None:
    if not Partner.all_objects.filter(tenant=tenant, pk=partner_id).exists():
        raise Http404()


def _require_idempotency_key(request) -> str:
    key = (request.headers.get('Idempotency-Key') or request.META.get('HTTP_IDEMPOTENCY_KEY') or '').strip()
    if not key:
        raise ValidationError({'Idempotency-Key': 'Header Idempotency-Key je obavezan.'})
    return key


def _conflict(exc):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_409_CONFLICT)


def _bad_request(exc):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)


def _resolution_error(exc: ExpenseAccountResolutionError):
    if getattr(exc, 'message_dict', None):
        return Response({'detail': exc.message_dict}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class _FinanceReadApiView(APIView):
    authentication_classes = [FinanceJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantFinanceReadPermission]
    http_method_names = ['get', 'head', 'options']

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


class _FinanceWriteApiView(APIView):
    authentication_classes = [FinanceJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantFinanceWritePermission]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


class JournalEntryListView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_journal_entries_list',
        parameters=JOURNAL_ENTRY_LIST_PARAMS,
        responses={
            200: PaginatedJournalEntriesSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_journal_entry_filters(request.query_params)
        except (ValueError, TypeError) as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_journal_entries(tenant, filters))


class JournalEntryDetailView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_journal_entries_retrieve',
        responses={
            200: JournalEntryDetailSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, pk: int):
        return Response(get_journal_entry(_require_tenant(request), pk))


class PartnerFinancialSummaryView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_partner_financial_summary',
        responses={200: PartnerFinancialSummarySerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        tenant = _require_tenant(request)
        _require_partner(tenant, pk)
        return Response(partner_financial_summary(tenant, pk))


def _query_flag_true(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


class PartnerSubledgerView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_partner_subledger',
        parameters=PARTNER_SUBLEDGER_PARAMS,
        responses={200: PartnerSubledgerListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        tenant = _require_tenant(request)
        _require_partner(tenant, pk)
        include_closed = _query_flag_true(request.query_params.get('include_closed'))
        return Response(partner_subledger_items(tenant, pk, include_closed=include_closed))


class PartnerStatementView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_partner_statement',
        parameters=PARTNER_STATEMENT_PARAMS,
        responses={200: PartnerStatementSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        tenant = _require_tenant(request)
        _require_partner(tenant, pk)
        raw_year = request.query_params.get('year')
        year = int(raw_year) if raw_year not in (None, '') else None
        direction = request.query_params.get('direction') or 'all'
        return Response(partner_statement(tenant, pk, year=year, direction=direction))


class DepositListCreateView(APIView):
    authentication_classes = [FinanceJWTAuthentication]

    def get_permissions(self):
        if self.request.method == 'GET':
            return [IsAuthenticated(), TenantFinanceReadPermission()]
        return [IsAuthenticated(), TenantFinanceWritePermission()]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)

    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_list',
        parameters=[
            OpenApiParameter(name='partner_id', type=int, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: DepositListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request):
        tenant = _require_tenant(request)
        partner_id = request.query_params.get('partner_id')
        pid = int(partner_id) if partner_id else None
        return Response(list_deposits(tenant=tenant, partner_id=pid))

    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_create',
        request=CreateDepositSerializer,
        responses={
            201: DepositSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def post(self, request):
        try:
            return Response(
                create_deposit(tenant=_require_tenant(request), data=request.data, user=request.user),
                status=status.HTTP_201_CREATED,
            )
        except DepositBadRequest as exc:
            return _bad_request(exc)
        except Http404:
            raise
        except Exception as exc:
            raise ValidationError({'detail': str(exc)}) from exc


class DepositDetailView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_retrieve',
        responses={200: DepositSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        return Response(get_deposit(tenant=_require_tenant(request), deposit_id=pk))


class DepositPostView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_post',
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
            ),
        ],
        request=None,
        responses={
            200: DepositSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        _require_idempotency_key(request)
        try:
            return Response(post_deposit(tenant=_require_tenant(request), deposit_id=pk, user=request.user))
        except DepositConflict as exc:
            return _conflict(exc)
        except DepositBadRequest as exc:
            return _bad_request(exc)


class DepositReturnView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_return',
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
            ),
        ],
        request=ReturnDepositSerializer,
        responses={
            200: DepositSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        _require_idempotency_key(request)
        try:
            return Response(
                return_deposit(
                    tenant=_require_tenant(request),
                    deposit_id=pk,
                    user=request.user,
                    data=request.data,
                )
            )
        except DepositConflict as exc:
            return _conflict(exc)
        except DepositBadRequest as exc:
            return _bad_request(exc)


class DepositReverseView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_reverse',
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
            ),
        ],
        request=None,
        responses={
            200: DepositSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        _require_idempotency_key(request)
        try:
            return Response(reverse_deposit(tenant=_require_tenant(request), deposit_id=pk, user=request.user))
        except DepositConflict as exc:
            return _conflict(exc)
        except DepositBadRequest as exc:
            return _bad_request(exc)


class OfficialDocumentCreateView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_official_documents_create',
        request=CreateOfficialDocumentSerializer,
        responses={
            201: OfficialDocumentSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request):
        try:
            return Response(
                create_official_document(
                    tenant=_require_tenant(request),
                    data=request.data,
                    file=request.FILES.get('file'),
                    user=request.user,
                ),
                status=status.HTTP_201_CREATED,
            )
        except OfficialDocumentConflict as exc:
            return _conflict(exc)
        except OfficialDocumentBadRequest as exc:
            return _bad_request(exc)


class OfficialDocumentDetailView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_official_documents_retrieve',
        responses={200: OfficialDocumentSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        return Response(get_official_document(tenant=_require_tenant(request), document_id=pk))


class OfficialDocumentRegisterView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_official_documents_register',
        request=CreateOfficialDocumentSerializer,
        responses={
            200: OfficialDocumentSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(
                register_official_document(
                    tenant=_require_tenant(request),
                    document_id=pk,
                    file=request.FILES.get('file'),
                )
            )
        except OfficialDocumentConflict as exc:
            return _conflict(exc)
        except OfficialDocumentBadRequest as exc:
            return _bad_request(exc)


class OfficialDocumentCancelView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_official_documents_cancel',
        request=None,
        responses={
            200: OfficialDocumentSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(cancel_official_document(tenant=_require_tenant(request), document_id=pk))
        except OfficialDocumentConflict as exc:
            return _conflict(exc)


class OfficialDocumentLinkJournalView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_official_documents_link_journal',
        request=LinkOfficialDocumentJournalSerializer,
        responses={
            200: OfficialDocumentSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(
                link_official_document_journal(
                    tenant=_require_tenant(request),
                    document_id=pk,
                    journal_entry_id=request.data.get('journal_entry_id'),
                )
            )
        except OfficialDocumentConflict as exc:
            return _conflict(exc)
        except OfficialDocumentBadRequest as exc:
            return _bad_request(exc)


class OfficialDocumentPostingProfileListView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_official_document_posting_profiles_list',
        responses={200: OfficialDocumentPostingProfileSerializer(many=True), 401: ERROR_401},
    )
    def get(self, request):
        return Response(list_official_document_posting_profiles(tenant=_require_tenant(request)))


class OfficialDocumentSetPostingProfileView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_official_documents_set_posting_profile',
        request=SetOfficialDocumentPostingProfileSerializer,
        responses={
            200: OfficialDocumentSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(
                set_official_document_posting_profile(
                    tenant=_require_tenant(request),
                    document_id=pk,
                    posting_profile_id=request.data.get('posting_profile_id'),
                )
            )
        except OfficialDocumentConflict as exc:
            return _conflict(exc)
        except OfficialDocumentBadRequest as exc:
            return _bad_request(exc)


class OfficialDocumentPostView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_official_documents_post',
        request=None,
        responses={
            200: OfficialDocumentSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
        },
    )
    def post(self, request, pk: int):
        _require_idempotency_key(request)
        try:
            return Response(
                post_official_document(
                    tenant=_require_tenant(request),
                    document_id=pk,
                    user=request.user,
                )
            )
        except OfficialDocumentConflict as exc:
            return _conflict(exc)
        except OfficialDocumentBadRequest as exc:
            return _bad_request(exc)


class DepositCancelView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_cancel',
        request=None,
        responses={
            200: DepositSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(cancel_deposit(tenant=_require_tenant(request), deposit_id=pk))
        except DepositConflict as exc:
            return _conflict(exc)


class ExpenseApproveView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_expenses_approve',
        request=None,
        responses={
            200: ExpenseApproveResponseSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(
                approve_expense_for_posting(
                    tenant=_require_tenant(request),
                    expense_id=pk,
                    user=request.user,
                )
            )
        except ExpenseApproveConflict as exc:
            return Response(
                {'code': exc.code, 'detail': exc.detail},
                status=status.HTTP_409_CONFLICT,
            )
        except ExpenseApproveBadRequest as exc:
            return Response(
                {'code': exc.code, 'detail': exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )


class ChartOfAccountsListView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_chart_of_accounts_list',
        parameters=CHART_OF_ACCOUNTS_PARAMS,
        responses={200: ChartOfAccountsListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request):
        return Response(
            list_postable_accounts(
                tenant=_require_tenant(request),
                search=request.query_params.get('search') or '',
            )
        )


class ExpensePostingPreviewView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_expenses_posting_preview',
        responses={
            200: ExpensePostingPreviewSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, pk: int):
        try:
            return Response(posting_preview(tenant=_require_tenant(request), expense_id=pk))
        except ExpenseAccountResolutionError as exc:
            return _resolution_error(exc)


class ExpenseDraftPatchView(_FinanceWriteApiView):
    http_method_names = ['patch', 'head', 'options']

    @extend_schema(
        tags=['finance'],
        operation_id='finance_expenses_draft_patch',
        request=ExpenseDraftPatchSerializer,
        responses={
            200: ExpenseApproveResponseSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def patch(self, request, pk: int):
        ser = ExpenseDraftPatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        kwargs = {}
        if 'category_id' in ser.validated_data:
            kwargs['category_id'] = ser.validated_data['category_id']
        if 'expense_account_id' in ser.validated_data:
            kwargs['expense_account_id'] = ser.validated_data['expense_account_id']
        if 'cost_center_id' in ser.validated_data:
            kwargs['cost_center_id'] = ser.validated_data['cost_center_id']
        try:
            return Response(
                update_draft_expense_posting(
                    tenant=_require_tenant(request),
                    expense_id=pk,
                    **kwargs,
                )
            )
        except ExpenseApproveConflict as exc:
            return Response(
                {'code': exc.code, 'detail': exc.detail},
                status=status.HTTP_409_CONFLICT,
            )
        except ExpenseApproveBadRequest as exc:
            return Response(
                {'code': exc.code, 'detail': exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ExpenseAccountResolutionError as exc:
            return _resolution_error(exc)
        except DjangoValidationError as exc:
            return Response({'detail': exc.message_dict}, status=status.HTTP_400_BAD_REQUEST)


def _pf_conflict(exc: PrivateFundsConflict):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_409_CONFLICT)


def _pf_bad_request(exc: PrivateFundsBadRequest):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)


class PrivateFundsClaimDetailView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_private_funds_claim_detail',
        responses={200: PrivateFundsClaimSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        return Response(get_claim(tenant=_require_tenant(request), claim_id=pk))


class PrivateFundsClaimCreateView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_private_funds_claim_create',
        request=CreatePrivateFundsClaimSerializer,
        responses={
            201: PrivateFundsClaimSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request):
        _require_idempotency_key(request)
        ser = CreatePrivateFundsClaimSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            dto = create_claim(
                tenant=_require_tenant(request),
                data=ser.validated_data,
                user=request.user,
            )
            return Response(dto, status=status.HTTP_201_CREATED)
        except PrivateFundsConflict as exc:
            return _pf_conflict(exc)
        except PrivateFundsBadRequest as exc:
            return _pf_bad_request(exc)


class PrivateFundsClaimPostView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_private_funds_claim_post',
        request=None,
        responses={
            200: PrivateFundsClaimSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        _require_idempotency_key(request)
        try:
            return Response(
                post_claim(
                    tenant=_require_tenant(request),
                    claim_id=pk,
                    user=request.user,
                )
            )
        except PrivateFundsConflict as exc:
            return _pf_conflict(exc)
        except PrivateFundsBadRequest as exc:
            return _pf_bad_request(exc)


class CostCenterListCreateView(APIView):
    authentication_classes = [FinanceJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantFinanceReadPermission]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), TenantFinanceWritePermission()]
        return super().get_permissions()

    @extend_schema(
        tags=['finance'],
        operation_id='finance_cost_centers_list',
        responses={200: CostCenterListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request):
        include_inactive = str(request.query_params.get('include_inactive') or '').lower() in {
            '1', 'true', 'yes',
        }
        return Response(
            list_cost_centers(tenant=_require_tenant(request), include_inactive=include_inactive)
        )

    @extend_schema(
        tags=['finance'],
        operation_id='finance_cost_centers_create',
        request=CostCenterWriteSerializer,
        responses={201: CostCenterSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def post(self, request):
        ser = CostCenterWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            return Response(
                create_cost_center(tenant=_require_tenant(request), data=ser.validated_data),
                status=status.HTTP_201_CREATED,
            )
        except DjangoValidationError as exc:
            return Response({'detail': exc.message_dict}, status=status.HTTP_400_BAD_REQUEST)


class CostCenterDetailView(_FinanceWriteApiView):
    http_method_names = ['patch', 'head', 'options']

    @extend_schema(
        tags=['finance'],
        operation_id='finance_cost_centers_partial_update',
        request=CostCenterPatchSerializer,
        responses={200: CostCenterSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def patch(self, request, pk: int):
        ser = CostCenterPatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            return Response(
                update_cost_center(
                    tenant=_require_tenant(request),
                    cost_center_id=pk,
                    data=ser.validated_data,
                )
            )
        except DjangoValidationError as exc:
            return Response({'detail': exc.message_dict}, status=status.HTTP_400_BAD_REQUEST)


class CostCenterReportView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_cost_centers_report',
        parameters=COST_CENTER_REPORT_PARAMS,
        responses={200: CostCenterReportSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request):
        try:
            year = int(request.query_params.get('year'))
            month = int(request.query_params.get('month'))
        except (TypeError, ValueError):
            return Response(
                {'detail': 'year i month su obavezni.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if month < 1 or month > 12:
            return Response({'detail': 'month mora biti 1–12.'}, status=status.HTTP_400_BAD_REQUEST)
        cumulative = str(request.query_params.get('cumulative') or '1').lower() not in {
            '0', 'false', 'no',
        }
        return Response(
            cost_center_report(
                _require_tenant(request),
                year,
                month,
                cumulative=cumulative,
            )
        )
