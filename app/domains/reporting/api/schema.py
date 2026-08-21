"""OpenAPI schema for document read API — DTO allowlist (ADR-0020)."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers

from config.schema_common import money_field


class ProvenancedSerializer(serializers.Serializer):
    value = serializers.JSONField(allow_null=True)
    reason = serializers.ChoiceField(
        choices=['not_recorded', 'not_provable', 'not_applicable'],
        allow_null=True,
    )
    source = serializers.CharField(allow_null=True)


class PostingBlockSerializer(serializers.Serializer):
    state = ProvenancedSerializer()
    entry_number = ProvenancedSerializer()
    entry_date = ProvenancedSerializer()
    fiscal_period = ProvenancedSerializer()
    fiscal_locked = ProvenancedSerializer()


class SubledgerBlockSerializer(serializers.Serializer):
    state = ProvenancedSerializer()
    open_amount = ProvenancedSerializer()
    original_amount = ProvenancedSerializer()
    aging_bucket = ProvenancedSerializer()
    days = ProvenancedSerializer()


class BankBlockSerializer(serializers.Serializer):
    match_status = ProvenancedSerializer()


class PaymentOrderBlockSerializer(serializers.Serializer):
    status = ProvenancedSerializer()
    creditor_iban = ProvenancedSerializer()


class PeriodReturnSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    version = serializers.IntegerField()
    status = serializers.CharField()
    superseded_by_id = serializers.IntegerField(allow_null=True)
    submitted_at = serializers.CharField(allow_null=True)
    has_active_submission_event = serializers.BooleanField()
    submission_nos = serializers.ListField(child=serializers.CharField())


class VatBlockSerializer(serializers.Serializer):
    lifecycle = ProvenancedSerializer()
    ledger_type = ProvenancedSerializer()
    period = ProvenancedSerializer()
    boxes = ProvenancedSerializer()
    document_in_submitted_return = ProvenancedSerializer()
    period_returns = PeriodReturnSerializer(many=True)
    disclaimer = serializers.CharField(allow_null=True)


class EracunBlockSerializer(serializers.Serializer):
    as4_status = ProvenancedSerializer()
    message_id = ProvenancedSerializer()
    source = ProvenancedSerializer()


class FiscalBlockSerializer(serializers.Serializer):
    jir = ProvenancedSerializer()
    zki = ProvenancedSerializer()


class AmountsBlockSerializer(serializers.Serializer):
    currency = serializers.CharField(allow_null=True)
    net = serializers.CharField(allow_null=True)
    vat = serializers.CharField(allow_null=True)
    gross = serializers.CharField(allow_null=True)
    fx_rate = ProvenancedSerializer()


class DocumentSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=['invoice', 'expense', 'deposit'])
    direction = serializers.ChoiceField(choices=['incoming', 'outgoing', 'deposit'])
    internal_number = serializers.CharField(allow_blank=True, allow_null=True)
    source_number = serializers.CharField(allow_blank=True, allow_null=True)
    partner_name = serializers.CharField(allow_blank=True, allow_null=True)
    partner_oib = serializers.CharField(allow_blank=True, allow_null=True)
    partner_vat_number = serializers.CharField(allow_blank=True, allow_null=True)
    document_date = serializers.CharField(allow_null=True)
    due_date = serializers.CharField(allow_null=True)
    document_status = ProvenancedSerializer()
    operational_status = ProvenancedSerializer()
    posting = PostingBlockSerializer()
    subledger = SubledgerBlockSerializer()
    bank = BankBlockSerializer()
    payment_order = PaymentOrderBlockSerializer()
    vat = VatBlockSerializer()
    eracun = EracunBlockSerializer()
    fiscal = FiscalBlockSerializer()
    controls = serializers.ListField(child=serializers.CharField())
    notices = serializers.ListField(child=serializers.CharField())
    amounts = AmountsBlockSerializer()


class DocumentItemSerializer(serializers.Serializer):
    item_name = serializers.CharField()
    quantity = serializers.CharField()
    unit_price = serializers.CharField()
    tax_rate = serializers.CharField()
    vat_procedure = serializers.CharField(allow_blank=True, allow_null=True)
    line_total = serializers.CharField()


class JournalLineSerializer(serializers.Serializer):
    account_code = serializers.CharField()
    account_name = serializers.CharField()
    debit = money_field()
    credit = money_field()
    description = serializers.CharField(allow_blank=True)


class DocumentPaymentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    payment_number = serializers.CharField(allow_blank=True)
    amount = money_field()
    payment_date = serializers.CharField(allow_null=True)
    payment_method = serializers.CharField(allow_blank=True)
    status = serializers.CharField()


class AllocationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    amount = money_field()
    created_at = serializers.CharField()


class LedgerEntrySerializer(serializers.Serializer):
    ledger_type = serializers.CharField()
    vat_box = serializers.CharField(allow_blank=True, allow_null=True)
    vat_rate = serializers.CharField(allow_null=True)
    base_amount = money_field()
    vat_amount = money_field()
    entry_category = serializers.CharField(allow_blank=True)
    entry_date = serializers.CharField(allow_null=True)
    document_number = serializers.CharField(allow_blank=True)


class AttachmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    original_filename = serializers.CharField()
    created_at = serializers.CharField(allow_null=True)
    uploaded_by = serializers.CharField(allow_null=True)
    download_available = ProvenancedSerializer()
    kind = serializers.CharField(required=False, allow_null=True)


class SupplierAddressSerializer(serializers.Serializer):
    street = serializers.CharField(allow_null=True)
    postal_code = serializers.CharField(allow_null=True)
    city = serializers.CharField(allow_null=True)


class SupplierSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    name = serializers.CharField(allow_null=True, allow_blank=True)
    country_code = serializers.CharField(allow_null=True, allow_blank=True)
    address = SupplierAddressSerializer()
    oib = serializers.CharField(allow_null=True, allow_blank=True)
    vat_id = serializers.CharField(allow_null=True, allow_blank=True)
    tax_id = serializers.CharField(allow_null=True, allow_blank=True)
    primary_iban = serializers.CharField(allow_null=True, allow_blank=True)


class IncomingDocumentMetaSerializer(serializers.Serializer):
    issue_date = serializers.CharField(allow_null=True)
    delivery_date = serializers.CharField(allow_null=True)
    due_date = serializers.CharField(allow_null=True)
    received_at = serializers.CharField(allow_null=True)
    currency = serializers.CharField(allow_null=True)
    business_process = serializers.CharField(allow_null=True)
    source_label = serializers.CharField(allow_null=True)
    format = serializers.CharField(allow_null=True)
    primary_reference = serializers.CharField(allow_null=True)


class IncomingLifecycleStatusSerializer(serializers.Serializer):
    document = serializers.CharField(allow_null=True)
    workflow = serializers.CharField(allow_null=True)
    integration = serializers.CharField(allow_null=True)
    posting = serializers.CharField(allow_null=True)
    vat = serializers.CharField(allow_null=True)
    subledger = serializers.CharField(allow_null=True)
    payment = serializers.CharField(allow_null=True)


class AccountingLineSerializer(serializers.Serializer):
    account_code = serializers.CharField(allow_null=True)
    account_name = serializers.CharField(allow_null=True)
    partner_name = serializers.CharField(allow_null=True)
    debit = serializers.CharField(allow_null=True)
    credit = serializers.CharField(allow_null=True)
    description = serializers.CharField(allow_blank=True)


class AccountingBlockSerializer(serializers.Serializer):
    journal_entry_id = serializers.IntegerField(allow_null=True)
    entry_number = serializers.CharField(allow_null=True)
    entry_date = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    debit_total = serializers.CharField(allow_null=True)
    credit_total = serializers.CharField(allow_null=True)
    lines = AccountingLineSerializer(many=True)


class VatContextRateSerializer(serializers.Serializer):
    rate = serializers.CharField(allow_null=True)
    base = serializers.CharField(allow_null=True)
    vat = serializers.CharField(allow_null=True)


class VatContextSerializer(serializers.Serializer):
    period = serializers.CharField(allow_null=True)
    recorded = serializers.BooleanField()
    deductible = serializers.BooleanField(allow_null=True)
    total_base = serializers.CharField(allow_null=True)
    total_vat = serializers.CharField(allow_null=True)
    rates = VatContextRateSerializer(many=True)


class SubledgerAllocationContextSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    amount = serializers.CharField(allow_null=True)
    created_at = serializers.CharField(allow_null=True)
    journal_entry_id = serializers.IntegerField(allow_null=True)
    status = serializers.CharField(allow_null=True)


class SubledgerContextSerializer(serializers.Serializer):
    item_id = serializers.IntegerField(allow_null=True)
    state = serializers.CharField(allow_null=True)
    original_amount = serializers.CharField(allow_null=True)
    allocated_amount = serializers.CharField(allow_null=True)
    open_amount = serializers.CharField(allow_null=True)
    due_date = serializers.CharField(allow_null=True)
    allocations = SubledgerAllocationContextSerializer(many=True)


class PaymentBlockSerializer(serializers.Serializer):
    matched = serializers.BooleanField()
    date = serializers.CharField(allow_null=True)
    amount = serializers.CharField(allow_null=True)
    account_mask = serializers.CharField(allow_null=True)
    reference = serializers.CharField(allow_null=True)
    reconcile_status = serializers.CharField(allow_null=True)
    bank_transaction_id = serializers.IntegerField(allow_null=True)


class SettlementTrailObligationSerializer(serializers.Serializer):
    amount = serializers.CharField(allow_null=True)
    journal_entry_id = serializers.IntegerField(allow_null=True)
    entry_number = serializers.CharField(allow_null=True)
    entry_date = serializers.CharField(allow_null=True)


class SettlementTrailClosingSerializer(serializers.Serializer):
    kind = serializers.CharField()
    amount = serializers.CharField(allow_null=True)
    journal_entry_id = serializers.IntegerField(allow_null=True)
    entry_number = serializers.CharField(allow_null=True)
    allocation_id = serializers.IntegerField()
    bank_transaction_id = serializers.IntegerField(allow_null=True)
    bank_statement_id = serializers.IntegerField(allow_null=True)
    counterparty_name = serializers.CharField(allow_null=True)
    private_funds_claim_id = serializers.IntegerField(allow_null=True)
    claim_number = serializers.CharField(allow_null=True)
    partner_id = serializers.IntegerField(allow_null=True)
    partner_name = serializers.CharField(allow_null=True)
    label = serializers.CharField()


class SettlementTrailSystemEntrySerializer(serializers.Serializer):
    kind = serializers.CharField()
    amount = serializers.CharField(allow_null=True)
    journal_entry_id = serializers.IntegerField()
    entry_number = serializers.CharField(allow_null=True)
    note = serializers.CharField()


class SettlementTrailWarningSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class SettlementTrailTotalsSerializer(serializers.Serializer):
    obligation = serializers.CharField(allow_null=True)
    allocated = serializers.CharField(allow_null=True)
    open = serializers.CharField(allow_null=True)


class SettlementTrailSerializer(serializers.Serializer):
    obligation = SettlementTrailObligationSerializer(allow_null=True)
    closings = SettlementTrailClosingSerializer(many=True)
    system_entries = SettlementTrailSystemEntrySerializer(many=True)
    warnings = SettlementTrailWarningSerializer(many=True)
    totals = SettlementTrailTotalsSerializer()


class LineClassificationSerializer(serializers.Serializer):
    scheme = serializers.CharField(allow_null=True)
    code = serializers.CharField(allow_null=True)


class IncomingLineSerializer(serializers.Serializer):
    position = serializers.IntegerField()
    classification = LineClassificationSerializer(allow_null=True)
    name = serializers.CharField(allow_null=True)
    description = serializers.CharField(allow_null=True)
    unit = serializers.CharField(allow_null=True)
    quantity = serializers.CharField(allow_null=True)
    unit_price = serializers.CharField(allow_null=True)
    vat_rate = serializers.CharField(allow_null=True)
    net_amount = serializers.CharField(allow_null=True)
    vat_amount = serializers.CharField(allow_null=True)
    gross_amount = serializers.CharField(allow_null=True)


class IncomingChargeSerializer(serializers.Serializer):
    code = serializers.CharField(allow_null=True)
    type = serializers.CharField(allow_null=True)
    description = serializers.CharField(allow_null=True)
    amount = serializers.CharField(allow_null=True)
    vat_rate = serializers.CharField(allow_null=True)


class TaxSummaryRowSerializer(serializers.Serializer):
    rate = serializers.CharField(allow_null=True)
    base = serializers.CharField(allow_null=True)
    vat = serializers.CharField(allow_null=True)


class IncomingTotalsSerializer(serializers.Serializer):
    line_net = serializers.CharField(allow_null=True)
    charges_total = serializers.CharField(allow_null=True)
    allowances_total = serializers.CharField(allow_null=True)
    vat_total = serializers.CharField(allow_null=True)
    grand_total = serializers.CharField(allow_null=True)
    prepaid = serializers.CharField(allow_null=True)
    payable = serializers.CharField(allow_null=True)
    currency = serializers.CharField(allow_null=True)


class DocumentReferenceSerializer(serializers.Serializer):
    type = serializers.CharField()
    value = serializers.CharField()


class IntegrationBlockSerializer(serializers.Serializer):
    source = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    received_at = serializers.CharField(allow_null=True)
    external_id = serializers.CharField(allow_null=True)
    external_view_url = serializers.URLField(allow_null=True)


class TechnicalBlockSerializer(serializers.Serializer):
    message_id = serializers.CharField(allow_null=True)
    parser_version = serializers.CharField(allow_null=True)
    imported_at = serializers.CharField(allow_null=True)
    source_hash = serializers.CharField(allow_null=True)


class DocumentDetailSerializer(DocumentSummarySerializer):
    partner_id = serializers.IntegerField(allow_null=True)
    description = serializers.CharField(allow_blank=True, allow_null=True)
    notes = serializers.CharField(allow_blank=True, allow_null=True)
    created_at = serializers.CharField(allow_null=True)
    updated_at = serializers.CharField(allow_null=True)
    created_by = serializers.CharField(allow_null=True)
    items = DocumentItemSerializer(many=True)
    service_date = serializers.CharField(allow_null=True)
    journal_lines = JournalLineSerializer(many=True)
    payments = DocumentPaymentSerializer(many=True)
    allocations = AllocationSerializer(many=True)
    ledger_entries = LedgerEntrySerializer(many=True)
    attachments = AttachmentSerializer(many=True)
    ubl_available = serializers.BooleanField()
    pdf_available = serializers.BooleanField()
    as_of = serializers.CharField()
    # PR A incoming enrichment (null/absent on outgoing/deposit)
    number = serializers.CharField(required=False, allow_null=True)
    supplier = SupplierSerializer(required=False)
    document = IncomingDocumentMetaSerializer(required=False)
    status = IncomingLifecycleStatusSerializer(required=False)
    lines = IncomingLineSerializer(many=True, required=False)
    charges = IncomingChargeSerializer(many=True, required=False)
    tax_summary = TaxSummaryRowSerializer(many=True, required=False)
    totals = IncomingTotalsSerializer(required=False)
    references = DocumentReferenceSerializer(many=True, required=False)
    integration = IntegrationBlockSerializer(required=False)
    technical = TechnicalBlockSerializer(required=False)
    # PR C accounting context (incoming only; avoid colliding with provenanced vat/subledger)
    accounting = AccountingBlockSerializer(required=False)
    vat_context = VatContextSerializer(required=False)
    subledger_context = SubledgerContextSerializer(required=False)
    payment = PaymentBlockSerializer(required=False)
    settlement_trail = SettlementTrailSerializer(required=False)


class CurrencySummarySerializer(serializers.Serializer):
    outgoing_count = serializers.IntegerField()
    incoming_count = serializers.IntegerField()
    outgoing_gross = serializers.CharField(allow_null=True)
    incoming_gross = serializers.CharField(allow_null=True)
    open_receivables = serializers.CharField(allow_null=True)
    open_payables = serializers.CharField(allow_null=True)


class DocumentListSummarySerializer(serializers.Serializer):
    by_currency = serializers.DictField(child=CurrencySummarySerializer())


class PaginatedDocumentsSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = DocumentSummarySerializer(many=True)
    summary = DocumentListSummarySerializer()


DOCUMENT_LIST_PARAMS = [
    OpenApiParameter(
        'direction',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        enum=['incoming', 'outgoing', 'deposit'],
    ),
    OpenApiParameter('status', OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter('search', OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter('year', OpenApiTypes.INT, OpenApiParameter.QUERY),
    OpenApiParameter('month', OpenApiTypes.INT, OpenApiParameter.QUERY),
    OpenApiParameter('partner', OpenApiTypes.INT, OpenApiParameter.QUERY, description='Partner id'),
    OpenApiParameter('oib', OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter('date_from', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter('date_to', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter('due_from', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter('due_to', OpenApiTypes.DATE, OpenApiParameter.QUERY),
    OpenApiParameter('amount_min', OpenApiTypes.NUMBER, OpenApiParameter.QUERY),
    OpenApiParameter('amount_max', OpenApiTypes.NUMBER, OpenApiParameter.QUERY),
    OpenApiParameter('currency', OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter(
        'view',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        enum=[
            'attention',
            'unpaid_outgoing',
            'overdue_outgoing',
            'incoming_ready_to_pay',
            'partially_paid',
            'bank_unmatched',
            'unposted',
            'vat_pending',
            'vat_mismatch',
            'eracun_rejected',
            'possible_duplicates',
        ],
    ),
    OpenApiParameter('page', OpenApiTypes.INT, OpenApiParameter.QUERY),
    OpenApiParameter(
        'page_size',
        OpenApiTypes.INT,
        OpenApiParameter.QUERY,
        description='Default 20, max 100',
    ),
]

DOCUMENT_EXPORT_PARAMS = DOCUMENT_LIST_PARAMS + [
    OpenApiParameter(
        'format',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        enum=['csv', 'xlsx'],
        description='Export file type (default csv)',
    ),
]
