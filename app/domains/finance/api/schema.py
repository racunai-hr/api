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
    closed_count = serializers.IntegerField()
    closed_results = PartnerSubledgerItemSerializer(many=True)


PARTNER_SUBLEDGER_PARAMS = [
    OpenApiParameter(
        name='include_closed',
        type=OpenApiTypes.BOOL,
        location=OpenApiParameter.QUERY,
        required=False,
        description=(
            'When true, populate closed_results with closed SubledgerItem rows. '
            'closed_count is always returned.'
        ),
    ),
]


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


class OfficialDocumentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    official_kind = serializers.ChoiceField(choices=['tax_decision', 'other'])
    issuer_id = serializers.IntegerField()
    issuer_name = serializers.CharField()
    document_number = serializers.CharField()
    reference = serializers.CharField(allow_blank=True)
    issue_date = serializers.CharField(allow_null=True)
    due_date = serializers.CharField(allow_null=True)
    amount = serializers.CharField()
    currency = serializers.CharField()
    workflow_status = serializers.ChoiceField(choices=['draft', 'registered', 'cancelled'])
    original_filename = serializers.CharField(allow_blank=True)
    content_type = serializers.CharField(allow_blank=True)
    file_sha256 = serializers.CharField(allow_blank=True)
    file_size = serializers.IntegerField()
    has_file = serializers.BooleanField()
    related_fixed_asset_id = serializers.IntegerField(allow_null=True)
    posting_profile_id = serializers.IntegerField(allow_null=True)
    cost_center = serializers.DictField(allow_null=True)
    posting_profile_code = serializers.CharField(allow_null=True)
    posting_profile_name = serializers.CharField(allow_null=True)
    notes = serializers.CharField(allow_blank=True)
    created_at = serializers.CharField(allow_null=True)


class CreateOfficialDocumentSerializer(serializers.Serializer):
    official_kind = serializers.ChoiceField(choices=['tax_decision', 'other'], required=False)
    issuer_id = serializers.IntegerField()
    document_number = serializers.CharField()
    reference = serializers.CharField(required=False, allow_blank=True)
    issue_date = serializers.DateField()
    due_date = serializers.DateField(required=False, allow_null=True)
    amount = money_field()
    currency = serializers.CharField(required=False, default='EUR')
    related_fixed_asset_id = serializers.IntegerField(required=False, allow_null=True)
    posting_profile_id = serializers.IntegerField(required=False, allow_null=True)
    cost_center_id = serializers.IntegerField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    register = serializers.BooleanField(required=False)
    file = serializers.FileField(required=False)


class LinkOfficialDocumentJournalSerializer(serializers.Serializer):
    journal_entry_id = serializers.IntegerField()


class SetOfficialDocumentPostingProfileSerializer(serializers.Serializer):
    posting_profile_id = serializers.IntegerField()


class OfficialDocumentPostingProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    economic_effect = serializers.ChoiceField(choices=['capitalize', 'expense'])
    allowed_kinds = serializers.ListField(child=serializers.CharField())
    requires_fixed_asset = serializers.BooleanField()
    is_active = serializers.BooleanField()


class ExpenseApproveResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    expense_number = serializers.CharField()
    status = serializers.CharField()
    amount = serializers.CharField()
    currency = serializers.CharField()
    expense_date = serializers.CharField(allow_null=True)
    due_date = serializers.CharField(allow_null=True)
    supplier_id = serializers.IntegerField(allow_null=True)
    category_id = serializers.IntegerField(allow_null=True)
    expense_account_id = serializers.IntegerField(allow_null=True)
    expense_account_source = serializers.CharField(allow_blank=True)
    settlement_method = serializers.CharField(allow_blank=True)
    approved_by_id = serializers.IntegerField(allow_null=True)
    cost_center_id = serializers.IntegerField(allow_null=True)


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


class CostCenterRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.CharField(required=False)
    is_active = serializers.BooleanField(required=False)
    is_bookable = serializers.BooleanField(required=False)
    parent_id = serializers.IntegerField(allow_null=True, required=False)


class JournalEntryLineSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    account_code = serializers.CharField(allow_blank=True)
    account_name = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    debit = money_field()
    credit = money_field()
    cost_center = CostCenterRefSerializer(allow_null=True)


class JournalEntrySourceDocumentSerializer(serializers.Serializer):
    # Same choice set as DocumentSummary.direction so spectacular reuses DirectionEnum.
    # DTO never emits deposit — no /dokumenti detail route for that kind.
    direction = serializers.ChoiceField(choices=['incoming', 'outgoing', 'deposit', 'official'])
    id = serializers.IntegerField()
    label = serializers.CharField()


class JournalEntryDetailSerializer(JournalEntryListItemSerializer):
    as_of = serializers.CharField()
    reference = serializers.CharField(allow_blank=True)
    source_id = serializers.IntegerField(allow_null=True)
    source_document = JournalEntrySourceDocumentSerializer(allow_null=True)
    lines = JournalEntryLineSerializer(many=True)


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


class PartnerStatementAmountsSerializer(serializers.Serializer):
    debit = money_field()
    credit = money_field()
    balance = money_field()


class PartnerStatementRowSerializer(serializers.Serializer):
    kind = serializers.CharField()
    date = serializers.CharField()
    debit = money_field()
    credit = money_field()
    balance = money_field()
    label = serializers.CharField(required=False)
    direction = serializers.CharField(required=False)
    source_type = serializers.CharField(required=False)
    source_id = serializers.IntegerField(required=False)
    source_label = serializers.CharField(required=False)
    document_type_label = serializers.CharField(required=False)
    closing_kind = serializers.CharField(required=False)
    subledger_item_id = serializers.IntegerField(required=False)
    allocation_id = serializers.IntegerField(required=False)
    journal_entry_id = serializers.IntegerField(required=False)
    entry_number = serializers.CharField(required=False)


class PartnerStatementSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    year = serializers.IntegerField()
    available_years = serializers.ListField(child=serializers.IntegerField())
    currency = serializers.CharField()
    direction = serializers.CharField()
    opening_balance = PartnerStatementAmountsSerializer()
    rows = PartnerStatementRowSerializer(many=True)
    closing_balance = PartnerStatementAmountsSerializer()


PARTNER_STATEMENT_PARAMS = [
    OpenApiParameter(
        name='year',
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        required=False,
        description='Calendar year. Defaults to current year if available, else latest.',
    ),
    OpenApiParameter(
        name='direction',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        enum=['all', 'receivable', 'payable'],
        description='Filter rows by AR/AP direction (default all).',
    ),
]


class AccountRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    active = serializers.BooleanField()


class ChartOfAccountsListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = AccountRefSerializer(many=True)


CHART_OF_ACCOUNTS_PARAMS = [
    OpenApiParameter(
        name='postable',
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        required=False,
        description='v1 vraća samo knjiživa konta; 1 je zadano.',
    ),
    OpenApiParameter(
        name='search',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description='Filter po šifri ili nazivu konta.',
    ),
]


class ExpenseCategoryRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class PostingPlanLineSerializer(serializers.Serializer):
    amount_field = serializers.CharField()
    description = serializers.CharField()
    amount = money_field()
    debit = AccountRefSerializer()
    credit = AccountRefSerializer()
    debit_cost_center = CostCenterRefSerializer(allow_null=True)
    credit_cost_center = CostCenterRefSerializer(allow_null=True)


class ExpensePostingPreviewSerializer(serializers.Serializer):
    category = ExpenseCategoryRefSerializer(allow_null=True)
    expense_account = AccountRefSerializer(allow_null=True)
    account_source = serializers.CharField(allow_null=True)
    warnings = serializers.ListField(child=serializers.CharField())
    can_approve = serializers.BooleanField()
    lines = PostingPlanLineSerializer(many=True)


class ExpenseLineAccountPatchSerializer(serializers.Serializer):
    position = serializers.IntegerField(min_value=1)
    posting_account_id = serializers.IntegerField(allow_null=True)


class ExpenseDraftPatchSerializer(serializers.Serializer):
    category_id = serializers.IntegerField(required=False)
    expense_account_id = serializers.IntegerField(required=False, allow_null=True)
    cost_center_id = serializers.IntegerField(required=False, allow_null=True)
    line_accounts = ExpenseLineAccountPatchSerializer(many=True, required=False)


class CostCenterSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=['location', 'object', 'overhead', 'group'])
    is_active = serializers.BooleanField()
    is_bookable = serializers.BooleanField()
    notes = serializers.CharField(allow_blank=True)
    parent_id = serializers.IntegerField(allow_null=True)
    parent = CostCenterRefSerializer(allow_null=True)


class CostCenterListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = CostCenterSerializer(many=True)


class CostCenterWriteSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=['location', 'object', 'overhead', 'group'])
    parent_id = serializers.IntegerField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)


class CostCenterPatchSerializer(serializers.Serializer):
    code = serializers.CharField(required=False)
    name = serializers.CharField(required=False)
    kind = serializers.ChoiceField(required=False, choices=['location', 'object', 'overhead', 'group'])
    parent_id = serializers.IntegerField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)


class CostCenterReportAccountSerializer(serializers.Serializer):
    account_code = serializers.CharField()
    account_name = serializers.CharField()
    account_class = serializers.CharField()
    amount = money_field()


class CostCenterReportRowSerializer(serializers.Serializer):
    cost_center_id = serializers.IntegerField(allow_null=True)
    code = serializers.CharField(allow_blank=True)
    name = serializers.CharField()
    kind = serializers.CharField(allow_blank=True)
    parent_id = serializers.IntegerField(allow_null=True)
    parent_code = serializers.CharField(allow_null=True, required=False)
    total = money_field()
    accounts = CostCenterReportAccountSerializer(many=True, required=False)


class CostCenterReportSerializer(serializers.Serializer):
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    cumulative = serializers.BooleanField()
    total = money_field()
    assigned_total = money_field()
    unassigned_total = money_field()
    groups = CostCenterReportRowSerializer(many=True)
    results = CostCenterReportRowSerializer(many=True)


COST_CENTER_REPORT_PARAMS = [
    OpenApiParameter(
        name='year',
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
    OpenApiParameter(
        name='month',
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
    OpenApiParameter(
        name='cumulative',
        type=OpenApiTypes.BOOL,
        location=OpenApiParameter.QUERY,
        required=False,
    ),
]
