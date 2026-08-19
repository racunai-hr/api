"""OpenAPI schema for banking API — DTO allowlist only (ADR-0021)."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers

from config.schema_common import money_field

PAGE = OpenApiParameter(
    name='page',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    description='Page number (min 1, default 1)',
)
PAGE_SIZE = OpenApiParameter(
    name='page_size',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    description='Page size (default 20, max 100)',
)


class ConnectionSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    provider_code = serializers.CharField(allow_blank=True)
    last_sync_at = serializers.CharField(allow_null=True)


class BalanceSerializer(serializers.Serializer):
    balance_type = serializers.CharField()
    amount = money_field()
    currency = serializers.CharField()
    as_of = serializers.CharField(allow_null=True)
    source = serializers.CharField()
    is_stale = serializers.BooleanField()


class BankAccountSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    account_name = serializers.CharField()
    bank_name = serializers.CharField(allow_blank=True)
    account_number = serializers.CharField(allow_blank=True)
    iban = serializers.CharField(allow_blank=True)
    currency = serializers.CharField()
    status = serializers.CharField()
    is_active = serializers.BooleanField()
    connection = ConnectionSummarySerializer(allow_null=True)
    balances = BalanceSerializer(many=True)


class BankingOverviewSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    accounts = BankAccountSerializer(many=True)
    account_count_by_currency = serializers.DictField(
        child=serializers.IntegerField(),
        help_text='Per-currency account counts; no cross-currency total balance',
    )
    unmatched_transaction_count = serializers.IntegerField()
    suggested_transaction_count = serializers.IntegerField()
    statement_count = serializers.IntegerField()


class StatementListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    statement_number = serializers.CharField(allow_blank=True)
    bank_account_id = serializers.IntegerField()
    statement_date = serializers.DateField(allow_null=True)
    opening_balance = money_field()
    closing_balance = money_field()
    status = serializers.CharField()
    currency = serializers.CharField()
    imported_at = serializers.CharField(allow_null=True)
    reconciled_at = serializers.CharField(allow_null=True)
    transaction_count = serializers.IntegerField()


class StatementDetailSerializer(StatementListItemSerializer):
    match_summary = serializers.DictField(child=serializers.IntegerField())
    as_of = serializers.CharField()


class TransactionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    bank_statement_id = serializers.IntegerField()
    bank_account_id = serializers.IntegerField()
    transaction_date = serializers.DateField(allow_null=True)
    value_date = serializers.DateField(allow_null=True)
    amount = money_field()
    currency = serializers.CharField()
    transaction_type = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    reference = serializers.CharField(allow_blank=True)
    counterparty_name = serializers.CharField(allow_blank=True)
    counterparty_iban = serializers.CharField(allow_blank=True)
    external_id = serializers.CharField(allow_blank=True, allow_null=True)
    match_status = serializers.CharField()
    matched_payment_id = serializers.IntegerField(allow_null=True)
    matched_journal_entry_id = serializers.IntegerField(allow_null=True)


class PaymentOrderSerializer(serializers.Serializer):
    """Allowlist only — no SCA, tokens, provider secrets, or internal errors."""

    id = serializers.IntegerField()
    status = serializers.CharField()
    amount = money_field()
    currency = serializers.CharField()
    debtor_iban = serializers.CharField(help_text='Masked IBAN')
    creditor_iban = serializers.CharField(help_text='Masked IBAN')
    creditor_name = serializers.CharField(allow_blank=True)
    reference = serializers.CharField(allow_blank=True)
    created_at = serializers.CharField()


class ImportRunSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    run_uuid = serializers.CharField()
    status = serializers.CharField()
    source = serializers.CharField()
    format = serializers.CharField(allow_blank=True)
    original_filename = serializers.CharField(allow_blank=True)
    statements_processed = serializers.IntegerField()
    statements_created = serializers.IntegerField()
    statements_updated = serializers.IntegerField()
    transactions_processed = serializers.IntegerField()
    transactions_created = serializers.IntegerField()
    transactions_skipped = serializers.IntegerField()
    error_count = serializers.IntegerField()
    warnings = serializers.ListField(child=serializers.CharField())
    errors = serializers.ListField(child=serializers.CharField())
    created_at = serializers.CharField(allow_null=True)
    started_at = serializers.CharField(allow_null=True)
    finished_at = serializers.CharField(allow_null=True)


class ImportRunDetailSerializer(ImportRunSerializer):
    as_of = serializers.CharField()


class ImportRunCreateResponseSerializer(ImportRunSerializer):
    created = serializers.BooleanField()


class SyncRunSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    connection_id = serializers.IntegerField()
    status = serializers.CharField()
    transactions_created = serializers.IntegerField()
    transactions_skipped = serializers.IntegerField()
    last_error = serializers.CharField(allow_blank=True, allow_null=True)
    correlation_id = serializers.CharField(allow_blank=True, allow_null=True)
    created_at = serializers.CharField(allow_null=True)
    started_at = serializers.CharField(allow_null=True)
    finished_at = serializers.CharField(allow_null=True)


class SyncEnqueueResponseSerializer(SyncRunSerializer):
    created = serializers.BooleanField()
    auto_create_payments = serializers.BooleanField()


class SyncStatusSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    connection_id = serializers.IntegerField()
    connection_status = serializers.CharField()
    last_sync_at = serializers.CharField(allow_null=True)
    active_sync = SyncRunSerializer(allow_null=True)
    latest_sync = SyncRunSerializer(allow_null=True)


class PaginatedBankAccountsSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = BankAccountSerializer(many=True)


class PaginatedStatementsSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = StatementListItemSerializer(many=True)


class PaginatedTransactionsSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = TransactionSerializer(many=True)


class PaginatedPaymentOrdersSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = PaymentOrderSerializer(many=True)


class MatchRequestSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(choices=['payment', 'journal_entry'])
    target_id = serializers.IntegerField()


BANK_ACCOUNT_PARAMS = [PAGE, PAGE_SIZE]
STATEMENT_PARAMS = [
    OpenApiParameter('bank_account', OpenApiTypes.INT, OpenApiParameter.QUERY),
    OpenApiParameter(
        'status',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        enum=['imported', 'reconciled', 'archived'],
    ),
    OpenApiParameter('date_from', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter('date_to', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    PAGE,
    PAGE_SIZE,
]
TRANSACTION_PARAMS = [
    OpenApiParameter('bank_account', OpenApiTypes.INT, OpenApiParameter.QUERY),
    OpenApiParameter('statement', OpenApiTypes.INT, OpenApiParameter.QUERY),
    OpenApiParameter(
        'match_status',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        enum=['unmatched', 'suggested', 'matched'],
    ),
    OpenApiParameter(
        'transaction_type',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        enum=['debit', 'credit'],
    ),
    OpenApiParameter('date_from', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter('date_to', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    PAGE,
    PAGE_SIZE,
]
PAYMENT_ORDER_PARAMS = [
    OpenApiParameter('status', OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter('date_from', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter('date_to', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    PAGE,
    PAGE_SIZE,
]
