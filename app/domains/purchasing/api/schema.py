"""OpenAPI schema for purchasing OCR invoice import API."""

from __future__ import annotations

from rest_framework import serializers

from config.schema_common import money_field


class PurchasingConflictSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()


class PartnerDiffSerializer(serializers.Serializer):
    field = serializers.CharField()
    existing = serializers.CharField(allow_blank=True)
    extracted = serializers.CharField(allow_blank=True)


class SupplierExtractedSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True)
    oib = serializers.CharField(allow_blank=True)
    vat_number = serializers.CharField(allow_blank=True)
    address = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True)
    postal_code = serializers.CharField(allow_blank=True)
    country = serializers.CharField(allow_blank=True)
    country_code = serializers.CharField(allow_blank=True)
    iban = serializers.CharField(allow_blank=True)


class ExtractedInvoiceSerializer(serializers.Serializer):
    supplier = SupplierExtractedSerializer()
    invoice_number = serializers.CharField(allow_blank=True)
    issue_date = serializers.CharField(allow_blank=True)
    due_date = serializers.CharField(allow_null=True, required=False)
    currency = serializers.CharField()
    net_amount = serializers.CharField(allow_blank=True)
    tax_amount = serializers.CharField(allow_blank=True)
    total_amount = serializers.CharField(allow_blank=True)
    iban = serializers.CharField(allow_blank=True)
    vat_breakdown = serializers.ListField(child=serializers.DictField(), required=False)
    line_items = serializers.ListField(child=serializers.DictField(), required=False)


class PartnerMatchSerializer(serializers.Serializer):
    match = serializers.CharField()
    partner_id = serializers.IntegerField(allow_null=True)
    candidate_id = serializers.IntegerField(allow_null=True)
    name = serializers.CharField(allow_blank=True)
    tax_number = serializers.CharField(allow_blank=True)
    diff = PartnerDiffSerializer(many=True)


class DuplicateSerializer(serializers.Serializer):
    kind = serializers.CharField()
    expense_id = serializers.IntegerField(allow_null=True)
    label = serializers.CharField(allow_blank=True)
    detail = serializers.DictField()


class IncomingInvoiceImportSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    original_filename = serializers.CharField()
    content_type = serializers.CharField(allow_blank=True)
    file_sha256 = serializers.CharField()
    file_size = serializers.IntegerField()
    ocr_provider = serializers.CharField(allow_blank=True)
    ocr_model = serializers.CharField(allow_blank=True)
    ocr_schema_version = serializers.CharField(allow_blank=True)
    ocr_extracted_at = serializers.CharField(allow_null=True)
    extracted = ExtractedInvoiceSerializer()
    warnings = serializers.ListField(child=serializers.CharField())
    partner = PartnerMatchSerializer()
    duplicate = DuplicateSerializer()
    confirmed_expense_id = serializers.IntegerField(allow_null=True)
    last_error = serializers.CharField(allow_blank=True)
    created_at = serializers.CharField(allow_null=True)
    started_at = serializers.CharField(allow_null=True)
    finished_at = serializers.CharField(allow_null=True)
    created = serializers.BooleanField(required=False)


class CreatePartnerFromImportSerializer(serializers.Serializer):
    name = serializers.CharField()
    tax_number = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    postal_code = serializers.CharField()
    country = serializers.CharField(required=False, allow_blank=True)
    country_code = serializers.CharField(required=False, allow_blank=True)
    partner_type = serializers.CharField(required=False)
    iban = serializers.CharField(required=False, allow_blank=True)
    vat_number = serializers.CharField(required=False, allow_blank=True)


class ConfirmInvoiceImportSerializer(serializers.Serializer):
    invoice_number = serializers.CharField(required=False)
    issue_date = serializers.CharField(required=False)
    due_date = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    net_amount = money_field(required=False)
    tax_amount = money_field(required=False)
    total_amount = money_field(required=False)
    currency = serializers.CharField(required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    iban = serializers.CharField(required=False, allow_blank=True)
    duplicate_override = serializers.BooleanField(required=False)
