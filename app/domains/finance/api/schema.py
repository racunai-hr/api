"""OpenAPI schema for finance partner read API (ADR-0022)."""

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
