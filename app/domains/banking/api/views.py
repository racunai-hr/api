"""JWT banking API views (ADR-0021 — read Slice 3, write Slice 4)."""

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

from banking.reconciliation import MatchConflict
from banking.services.import_runs import IdempotencyConflict
from config.schema_common import ERROR_400, ERROR_401, ERROR_404, ERROR_409, ERROR_422
from domains.banking.api.authentication import BankingJWTAuthentication
from domains.banking.api.permissions import (
    TenantBankingReadPermission,
    TenantBankingWritePermission,
)
from domains.banking.api.schema import (
    BANK_ACCOUNT_PARAMS,
    PAYMENT_ORDER_PARAMS,
    STATEMENT_PARAMS,
    TRANSACTION_PARAMS,
    BankingOverviewSerializer,
    ImportRunCreateResponseSerializer,
    ImportRunDetailSerializer,
    MatchRequestSerializer,
    OpenItemCandidateListSerializer,
    PaginatedBankAccountsSerializer,
    PaginatedPaymentOrdersSerializer,
    PaginatedStatementsSerializer,
    PaginatedTransactionsSerializer,
    ReconcileOpenItemRequestSerializer,
    StatementDetailSerializer,
    SyncEnqueueResponseSerializer,
    SyncStatusSerializer,
    TransactionSerializer,
)
from domains.banking.read.filters import (
    parse_bank_account_filters,
    parse_payment_order_filters,
    parse_statement_filters,
    parse_transaction_filters,
)
from domains.banking.read.service import (
    get_connection_sync_status,
    get_overview,
    get_statement,
    get_statement_import,
    list_bank_accounts,
    list_payment_orders,
    list_statements,
    list_transactions,
)
from domains.banking.write.reconcile import (
    AlreadyReconciled,
    ReconcileIdempotencyConflict,
    list_open_item_candidates,
    reconcile_open_item_api,
)
from domains.banking.write.service import (
    match_transaction_api,
    start_sync_api,
    submit_import,
    unmatch_transaction_api,
)


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


class _BankingApiView(APIView):
    authentication_classes = [BankingJWTAuthentication]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


class _BankingReadApiView(_BankingApiView):
    permission_classes = [IsAuthenticated, TenantBankingReadPermission]
    http_method_names = ['get', 'head', 'options']


class _BankingWriteApiView(_BankingApiView):
    permission_classes = [IsAuthenticated, TenantBankingWritePermission]
    http_method_names = ['post', 'head', 'options']


class BankingOverviewView(_BankingReadApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_overview_retrieve',
        responses={
            200: BankingOverviewSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request):
        return Response(get_overview(_require_tenant(request)))


class BankAccountListView(_BankingReadApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_bank_accounts_list',
        parameters=BANK_ACCOUNT_PARAMS,
        responses={
            200: PaginatedBankAccountsSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_bank_account_filters(request.query_params)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_bank_accounts(tenant, filters))


class StatementListView(_BankingReadApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_statements_list',
        parameters=STATEMENT_PARAMS,
        responses={
            200: PaginatedStatementsSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_statement_filters(request.query_params)
        except (ValueError, TypeError) as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_statements(tenant, filters))


class StatementDetailView(_BankingReadApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_statements_retrieve',
        responses={
            200: StatementDetailSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, pk):
        return Response(get_statement(_require_tenant(request), pk))


class TransactionListView(_BankingReadApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_transactions_list',
        parameters=TRANSACTION_PARAMS,
        responses={
            200: PaginatedTransactionsSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_transaction_filters(request.query_params)
        except (ValueError, TypeError) as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_transactions(tenant, filters))


class PaymentOrderListView(_BankingReadApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_payment_orders_list',
        parameters=PAYMENT_ORDER_PARAMS,
        responses={
            200: PaginatedPaymentOrdersSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_payment_order_filters(request.query_params)
        except (ValueError, TypeError) as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_payment_orders(tenant, filters))


class StatementImportDetailView(_BankingReadApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_statement_imports_retrieve',
        responses={
            200: ImportRunDetailSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, pk):
        return Response(get_statement_import(_require_tenant(request), pk))


class ConnectionSyncStatusView(_BankingReadApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_connections_sync_status_retrieve',
        responses={
            200: SyncStatusSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, pk):
        return Response(get_connection_sync_status(_require_tenant(request), pk))


class StatementImportCreateView(_BankingWriteApiView):
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=['banking'],
        operation_id='banking_statement_imports_create',
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
                'properties': {
                    'file': {'type': 'string', 'format': 'binary'},
                },
                'required': ['file'],
            }
        },
        responses={
            202: ImportRunCreateResponseSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
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
        idempotency_key = request.headers.get('Idempotency-Key', '')
        try:
            content = upload.read()
            result = submit_import(
                tenant=tenant,
                actor=request.user,
                content=content,
                filename=getattr(upload, 'name', '') or '',
                idempotency_key=idempotency_key,
            )
        except (MatchConflict, IdempotencyConflict) as exc:
            return Response({'detail': _error_detail(exc)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(
                {'detail': _error_detail(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(result.payload, status=result.status_code)


class TransactionMatchView(_BankingWriteApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_transactions_match',
        request=MatchRequestSerializer,
        responses={
            200: TransactionSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
            422: ERROR_422,
        },
    )
    def post(self, request, pk):
        tenant = _require_tenant(request)
        target_type = request.data.get('target_type')
        target_id = request.data.get('target_id')
        try:
            target_id_int = int(target_id)
        except (TypeError, ValueError):
            return Response(
                {'detail': {'target_id': 'target_id mora biti cijeli broj.'}},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        try:
            payload = match_transaction_api(
                tenant=tenant,
                user=request.user,
                transaction_id=pk,
                target_type=target_type,
                target_id=target_id_int,
            )
        except MatchConflict as exc:
            return Response({'detail': _error_detail(exc)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(
                {'detail': _error_detail(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(payload, status=status.HTTP_200_OK)


class TransactionUnmatchView(_BankingWriteApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_transactions_unmatch',
        request=None,
        responses={
            200: TransactionSerializer,
            401: ERROR_401,
            404: ERROR_404,
            422: ERROR_422,
        },
    )
    def post(self, request, pk):
        tenant = _require_tenant(request)
        try:
            payload = unmatch_transaction_api(
                tenant=tenant,
                user=request.user,
                transaction_id=pk,
            )
        except DjangoValidationError as exc:
            return Response(
                {'detail': _error_detail(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(payload, status=status.HTTP_200_OK)


class TransactionOpenItemCandidatesView(_BankingReadApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_transactions_open_item_candidates',
        parameters=[
            OpenApiParameter('q', str, OpenApiParameter.QUERY, required=False),
        ],
        responses={
            200: OpenItemCandidateListSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, pk):
        tenant = _require_tenant(request)
        return Response(
            list_open_item_candidates(
                tenant=tenant,
                transaction_id=pk,
                q=request.query_params.get('q'),
            )
        )


class TransactionReconcileOpenItemView(_BankingWriteApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_transactions_reconcile_open_item',
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
            ),
        ],
        request=ReconcileOpenItemRequestSerializer,
        responses={
            200: TransactionSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: ERROR_409,
            422: ERROR_422,
        },
    )
    def post(self, request, pk):
        tenant = _require_tenant(request)
        raw_id = request.data.get('subledger_item_id')
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            return Response(
                {'detail': {'subledger_item_id': 'subledger_item_id mora biti cijeli broj.'}},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        idempotency_key = request.headers.get('Idempotency-Key', '')
        try:
            payload = reconcile_open_item_api(
                tenant=tenant,
                user=request.user,
                transaction_id=pk,
                subledger_item_id=item_id,
                idempotency_key=idempotency_key,
            )
        except (AlreadyReconciled, ReconcileIdempotencyConflict, MatchConflict) as exc:
            return Response({'detail': _error_detail(exc)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(
                {'detail': _error_detail(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(payload, status=status.HTTP_200_OK)


class ConnectionSyncEnqueueView(_BankingWriteApiView):
    @extend_schema(
        tags=['banking'],
        operation_id='banking_connections_sync_create',
        request=None,
        responses={
            202: SyncEnqueueResponseSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def post(self, request, pk):
        tenant = _require_tenant(request)
        payload = start_sync_api(tenant=tenant, user=request.user, connection_id=pk)
        return Response(payload, status=status.HTTP_202_ACCEPTED)
