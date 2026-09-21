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
    latest_return_version = serializers.IntegerField(allow_null=True)
    latest_return_status = serializers.CharField(allow_null=True)
    correction_in_progress = serializers.BooleanField()
    vat_due = money_field()
    submitted_at = serializers.CharField(allow_null=True)


class PdvPeriodListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = PdvPeriodSerializer(many=True)


class PdvPeriodWorkspaceSerializer(PdvPeriodSerializer):
    xml_integrity = serializers.CharField(allow_null=True)
    event_uuid = serializers.UUIDField(allow_null=True)
    has_confirmation = serializers.BooleanField()


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
    eporezna_identifier = serializers.UUIDField(
        required=False,
        help_text='Opcionalno. Portalni identifikator zaprimanja. Nikad XML Metapodaci/Identifikator.',
    )
    submitted_at = serializers.DateTimeField(
        required=False,
        help_text='Opcionalno. Vrijeme zaprimanja s portala. Uvoz XML-a koristi vrijeme importa.',
    )
    return_version = serializers.IntegerField()
    submitted_xml = serializers.FileField(
        required=False,
        help_text='Predani (potpisani) Obrazac PDV XML. Arhiva obrasca, nije potvrda zaprimanja.',
    )


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


class Tz2YearSerializer(serializers.Serializer):
    tax_year = serializers.IntegerField()
    version = serializers.IntegerField(allow_null=True)
    persisted = serializers.BooleanField()
    schema_version = serializers.CharField()
    mapping_version = serializers.IntegerField()
    period_from = serializers.CharField()
    period_to = serializers.CharField()
    rates = serializers.DictField()
    taxpayer = serializers.DictField()
    room_beds = serializers.IntegerField()
    aux_beds = serializers.IntegerField()
    room_bed_rate = money_field()
    aux_bed_rate = money_field()
    room_bed_total = money_field()
    aux_bed_total = money_field()
    total_assessed = money_field()
    amount_after_discount = money_field()
    payment_installments = serializers.BooleanField()
    installment_flag = serializers.CharField()
    installment_amount = money_field()
    ep_receipts = money_field()
    event_uuid = serializers.UUIDField(allow_null=True)
    current_submission = PdvSSubmissionSerializer(allow_null=True)
    submissions = PdvSSubmissionSerializer(many=True)


class Tz2SaveRequestSerializer(serializers.Serializer):
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    oib = serializers.CharField()
    municipality_code = serializers.CharField()
    city = serializers.CharField()
    street = serializers.CharField()
    house_number = serializers.CharField()
    room_beds = serializers.IntegerField()
    aux_beds = serializers.IntegerField()
    payment_installments = serializers.BooleanField()
    ep_receipts = serializers.CharField()
    room_bed_rate = serializers.CharField(required=False, allow_blank=True)
    aux_bed_rate = serializers.CharField(required=False, allow_blank=True)
