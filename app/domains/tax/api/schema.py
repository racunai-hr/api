"""OpenAPI schema for PDV / PDV-S tax API."""

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


class PdvPeriodWorkspaceSerializer(PdvPeriodSerializer):
    xml_integrity = serializers.CharField(allow_null=True)
    event_uuid = serializers.UUIDField(allow_null=True)


class PdvLedgerRebuildSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    total = serializers.IntegerField()


class PdvBoxesSerializer(serializers.Serializer):
    period = serializers.CharField()
    schema_version = serializers.CharField()
    mapping_version = serializers.IntegerField()
    vat_due = money_field()
    fields = serializers.DictField()


class PdvDraftSerializer(serializers.Serializer):
    period = serializers.CharField()
    return_version = serializers.IntegerField()
    return_status = serializers.CharField()
    xml_integrity = serializers.CharField()


class PdvSubmitRequestSerializer(serializers.Serializer):
    eporezna_identifier = serializers.UUIDField()
    submitted_at = serializers.DateTimeField()
    return_version = serializers.IntegerField()


class PdvSSubmitRequestSerializer(serializers.Serializer):
    eporezna_identifier = serializers.UUIDField()
    submitted_at = serializers.DateTimeField()


class SubmissionResultSerializer(serializers.Serializer):
    event_uuid = serializers.UUIDField()
    external_identifier = serializers.UUIDField()
    submitted_at = serializers.CharField()
    has_confirmation = serializers.BooleanField()


class ConfirmationResultSerializer(serializers.Serializer):
    event_uuid = serializers.UUIDField()
    has_confirmation = serializers.BooleanField()


class ConfirmationUploadSerializer(serializers.Serializer):
    confirmation = serializers.FileField()


class PdvSRowSerializer(serializers.Serializer):
    country_code = serializers.CharField()
    pdv_id = serializers.CharField()
    goods_value = money_field()
    services_value = money_field()


class PdvSSubmissionSerializer(serializers.Serializer):
    event_uuid = serializers.UUIDField()
    submission_no = serializers.IntegerField()
    submission_type = serializers.CharField()
    external_identifier = serializers.UUIDField()
    submitted_at = serializers.CharField(allow_null=True)
    has_confirmation = serializers.BooleanField()
    payload_hash = serializers.CharField()


class PdvSPeriodSerializer(serializers.Serializer):
    period = serializers.CharField()
    schema_version = serializers.CharField()
    row_count = serializers.IntegerField()
    total_goods = money_field()
    total_services = money_field()
    rows = PdvSRowSerializer(many=True)
    event_uuid = serializers.UUIDField(allow_null=True)
    current_submission = PdvSSubmissionSerializer(allow_null=True)
    submissions = PdvSSubmissionSerializer(many=True)
