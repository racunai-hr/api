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
    direction = serializers.ChoiceField(choices=['incoming', 'outgoing'])
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
    as_of = serializers.CharField()


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
        enum=['incoming', 'outgoing'],
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
