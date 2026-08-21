"""OpenAPI schema for finance partner read API and deposits (ADR-0022 / ADR-0024)."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers

from config.schema_common import money_field
from domains.finance.read.dto import SOURCE_TYPES


class PartnerFinancialSummarySerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    as_of_date = serializers.CharField()
    currency = serializers.CharField()
    receivables_open = money_field()
    payables_open = money_field()
    receivables_overdue = money_field()
    payables_overdue = money_field()
    net_balance = money_field()


class PartnerSubledgerItemSerializer(serializers.Serializer):
    item_id = serializers.IntegerField()
    partner_id = serializers.IntegerField()
    partner_name = serializers.CharField()
    direction = serializers.CharField()
    direction_label = serializers.CharField()
    source_type = serializers.CharField()
    source_id = serializers.IntegerField()
    source_label = serializers.CharField()
    original_amount = money_field()
    open_amount = money_field()
    due_date = serializers.CharField(allow_null=True)
    days_overdue = serializers.IntegerField()
    aging_bucket = serializers.CharField()
    status = serializers.CharField()


class PartnerSubledgerListSerializer(serializers.Serializer):
    as_of_date = serializers.CharField()
    partner_id = serializers.IntegerField()
    count = serializers.IntegerField()
    results = PartnerSubledgerItemSerializer(many=True)


class DepositSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    number = serializers.CharField()
    partner_id = serializers.IntegerField()
    partner_name = serializers.CharField()
    direction = serializers.CharField()
    amount = serializers.CharField()
    currency = serializers.CharField()
    deposit_date = serializers.CharField(allow_null=True)
    workflow_status = serializers.CharField()
    operational_status = serializers.CharField()
    open_amount = serializers.CharField()
    reference = serializers.CharField(allow_blank=True)
    notes = serializers.CharField(allow_blank=True)
    return_date = serializers.CharField(allow_null=True)
    return_bank_account_id = serializers.IntegerField(allow_null=True)
    given_journal_entry_id = serializers.IntegerField(allow_null=True)
    return_journal_entry_id = serializers.IntegerField(allow_null=True)
    reverse_journal_entry_id = serializers.IntegerField(allow_null=True)
    created_at = serializers.CharField(allow_null=True)


class DepositListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = DepositSerializer(many=True)


class CreateDepositSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    amount = money_field()
    currency = serializers.CharField(required=False, default='EUR')
    deposit_date = serializers.DateField()
    reference = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class ReturnDepositSerializer(serializers.Serializer):
    return_bank_account_id = serializers.IntegerField()
    return_date = serializers.DateField(required=False)
    amount = money_field(required=False)


class DepositConflictSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()


class ExpenseApproveResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    expense_number = serializers.CharField()
    status = serializers.CharField()
    amount = serializers.CharField()
    currency = serializers.CharField()
    expense_date = serializers.CharField(allow_null=True)
    due_date = serializers.CharField(allow_null=True)
    supplier_id = serializers.IntegerField(allow_null=True)
    settlement_method = serializers.CharField(allow_blank=True)
    approved_by_id = serializers.IntegerField(allow_null=True)


class PrivateFundsClaimSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    number = serializers.CharField()
    claim_type = serializers.CharField()
    partner_id = serializers.IntegerField()
    partner_name = serializers.CharField()
    amount = serializers.CharField()
    currency = serializers.CharField()
    claim_date = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    operational_status = serializers.CharField()
    open_amount = serializers.CharField()
    reference = serializers.CharField(allow_blank=True)
    notes = serializers.CharField(allow_blank=True)
    related_type = serializers.CharField()
    related_id = serializers.IntegerField()
    journal_entry_id = serializers.IntegerField(allow_null=True)
    created_at = serializers.CharField(allow_null=True)


class CreatePrivateFundsClaimSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    claim_type = serializers.ChoiceField(choices=['supplier_payment', 'deposit_funding'])
    amount = money_field()
    currency = serializers.CharField(required=False, default='EUR')
    claim_date = serializers.DateField()
    related_type = serializers.ChoiceField(choices=['expense', 'deposit'])
    related_id = serializers.IntegerField()
    reference = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class JournalEntryListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    entry_number = serializers.CharField()
    entry_date = serializers.DateField(allow_null=True)
    description = serializers.CharField(allow_blank=True)
    status = serializers.ChoiceField(choices=['draft', 'posted', 'reversed'])
    is_auto = serializers.BooleanField()
    source_type = serializers.ChoiceField(choices=list(SOURCE_TYPES))
    total_debit = money_field()
    total_credit = money_field()


class PaginatedJournalEntriesSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = JournalEntryListItemSerializer(many=True)


JOURNAL_ENTRY_LIST_PARAMS = [
    OpenApiParameter(
        'status',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        enum=['draft', 'posted', 'reversed'],
    ),
    OpenApiParameter('date_from', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter('date_to', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter(
        'search',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        description='Filter by entry_number, description, or reference',
    ),
    OpenApiParameter(
        name='page',
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        description='Page number (min 1, default 1)',
    ),
    OpenApiParameter(
        name='page_size',
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        description='Page size (default 20, max 100)',
    ),
]
