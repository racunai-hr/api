"""JWT banking API views (ADR-0021 — read Slice 3, write Slice 4)."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from banking.reconciliation import MatchConflict
from banking.services.import_runs import IdempotencyConflict
from domains.banking.api.authentication import BankingJWTAuthentication
from domains.banking.api.permissions import (
    TenantBankingReadPermission,
    TenantBankingWritePermission,
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
    def get(self, request):
        return Response(get_overview(_require_tenant(request)))


class BankAccountListView(_BankingReadApiView):
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_bank_account_filters(request.query_params)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_bank_accounts(tenant, filters))


class StatementListView(_BankingReadApiView):
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_statement_filters(request.query_params)
        except (ValueError, TypeError) as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_statements(tenant, filters))


class StatementDetailView(_BankingReadApiView):
    def get(self, request, pk):
        return Response(get_statement(_require_tenant(request), pk))


class TransactionListView(_BankingReadApiView):
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_transaction_filters(request.query_params)
        except (ValueError, TypeError) as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_transactions(tenant, filters))


class PaymentOrderListView(_BankingReadApiView):
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_payment_order_filters(request.query_params)
        except (ValueError, TypeError) as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_payment_orders(tenant, filters))


class StatementImportDetailView(_BankingReadApiView):
    def get(self, request, pk):
        return Response(get_statement_import(_require_tenant(request), pk))


class ConnectionSyncStatusView(_BankingReadApiView):
    def get(self, request, pk):
        return Response(get_connection_sync_status(_require_tenant(request), pk))


class StatementImportCreateView(_BankingWriteApiView):
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


class ConnectionSyncEnqueueView(_BankingWriteApiView):
    def post(self, request, pk):
        tenant = _require_tenant(request)
        payload = start_sync_api(tenant=tenant, user=request.user, connection_id=pk)
        return Response(payload, status=status.HTTP_202_ACCEPTED)
