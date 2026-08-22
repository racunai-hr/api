"""OpenAPI schema for PDV Razdoblja read-model."""

from __future__ import annotations

from rest_framework import serializers

from config.schema_common import money_field


class PdvPeriodSerializer(serializers.Serializer):
    period = serializers.CharField(help_text='Canonical period YYYY-MM')
    period_status = serializers.CharField()
    has_ledger = serializers.BooleanField()
    return_version = serializers.IntegerField(allow_null=True)
    return_status = serializers.CharField(allow_null=True)
    vat_due = money_field()
    submitted_at = serializers.CharField(allow_null=True)


class PdvPeriodListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = PdvPeriodSerializer(many=True)
