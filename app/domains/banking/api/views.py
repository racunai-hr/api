"""JWT banking read API views (ADR-0021 Slice 3 — read only)."""

from __future__ import annotations

from django.http import Http404
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from domains.banking.api.authentication import BankingJWTAuthentication
from domains.banking.api.permissions import TenantBankingReadPermission
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


class _BankingReadApiView(APIView):
    authentication_classes = [BankingJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantBankingReadPermission]
    http_method_names = ['get', 'head', 'options']

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


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
