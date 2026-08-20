"""OpenAPI schema for finance partner read API and deposits (ADR-0022 / ADR-0024)."""

from __future__ import annotations

from rest_framework import serializers

from config.schema_common import money_field


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
