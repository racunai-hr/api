"""OpenAPI schema for partners MDM API (ADR-0022)."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers

from config.schema_common import money_field

PAGE = OpenApiParameter(
    name='page',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    description='Page number (min 1, default 1)',
)
PAGE_SIZE = OpenApiParameter(
    name='page_size',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    description='Page size (default 20, max 100)',
)
FILTER = OpenApiParameter(
    name='filter',
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description='Default active-only; "all" = every status; "inactive" = inactive+blocked',
)
PARTNER_TYPE = OpenApiParameter(
    name='partner_type',
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description='customer|supplier|both|other|customers|suppliers',
)
STATUS = OpenApiParameter(
    name='status',
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description='Exact status filter (overrides default active when set)',
)
SEARCH = OpenApiParameter(
    name='search',
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description='Search name, code, tax number, VAT',
)
JURISDICTION = OpenApiParameter(
    name='jurisdiction',
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description='HR|EU|NON_EU (orthogonal to status; EU never includes HR)',
)

PARTNER_LIST_PARAMS = [FILTER, PARTNER_TYPE, STATUS, JURISDICTION, SEARCH, PAGE, PAGE_SIZE]


class PartnerConflictSerializer(serializers.Serializer):
    code = serializers.CharField()
    field = serializers.CharField()


class PartnerFieldErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    field = serializers.CharField()
    detail = serializers.CharField()


class PartnerListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    partner_code = serializers.CharField()
    name = serializers.CharField()
    short_name = serializers.CharField(allow_blank=True)
    partner_type = serializers.CharField()
    status = serializers.CharField()
    jurisdiction = serializers.CharField()
    country_code = serializers.CharField()
    tax_number = serializers.CharField(allow_blank=True)
    city = serializers.CharField()
    country = serializers.CharField()
    email = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)


class PartnerSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    partner_code = serializers.CharField()
    name = serializers.CharField()
    short_name = serializers.CharField(allow_blank=True)
    partner_type = serializers.CharField()
    status = serializers.CharField()
    jurisdiction = serializers.CharField()
    country_code = serializers.CharField()
    tax_number = serializers.CharField(allow_blank=True)
    vat_number = serializers.CharField(allow_blank=True)
    registration_number = serializers.CharField(allow_blank=True)
    address = serializers.CharField()
    city = serializers.CharField()
    postal_code = serializers.CharField()
    country = serializers.CharField()
    email = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)
    mobile = serializers.CharField(allow_blank=True)
    fax = serializers.CharField(allow_blank=True)
    website = serializers.CharField(allow_blank=True)
    payment_terms = serializers.IntegerField()
    credit_limit = money_field()
    discount_percentage = money_field()
    notes = serializers.CharField(allow_blank=True)
    internal_notes = serializers.CharField(allow_blank=True)
    created_at = serializers.CharField(allow_null=True)
    updated_at = serializers.CharField(allow_null=True)


class PartnerWriteSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    short_name = serializers.CharField(required=False, allow_blank=True)
    partner_type = serializers.CharField(required=False)
    status = serializers.CharField(required=False)
    country_code = serializers.CharField(required=False)
    tax_number = serializers.CharField(required=False, allow_blank=True)
    vat_number = serializers.CharField(required=False, allow_blank=True)
    registration_number = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(required=False)
    city = serializers.CharField(required=False)
    postal_code = serializers.CharField(required=False)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    mobile = serializers.CharField(required=False, allow_blank=True)
    fax = serializers.CharField(required=False, allow_blank=True)
    website = serializers.CharField(required=False, allow_blank=True)
    payment_terms = serializers.IntegerField(required=False)
    credit_limit = serializers.CharField(required=False)
    discount_percentage = serializers.CharField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    internal_notes = serializers.CharField(required=False, allow_blank=True)


class PaginatedPartnersSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = PartnerListItemSerializer(many=True)


class ContactSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    partner_id = serializers.IntegerField()
    contact_type = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    full_name = serializers.CharField()
    position = serializers.CharField(allow_blank=True)
    department = serializers.CharField(allow_blank=True)
    email = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)
    mobile = serializers.CharField(allow_blank=True)
    notes = serializers.CharField(allow_blank=True)
    is_primary = serializers.BooleanField()
    is_active = serializers.BooleanField()
    created_at = serializers.CharField(allow_null=True)


class ContactWriteSerializer(serializers.Serializer):
    contact_type = serializers.CharField(required=False)
    first_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)
    position = serializers.CharField(required=False, allow_blank=True)
    department = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    mobile = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    is_primary = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)


class ContactListSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    count = serializers.IntegerField()
    results = ContactSerializer(many=True)


class PartnerBankAccountSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    partner_id = serializers.IntegerField()
    bank_name = serializers.CharField(allow_blank=True)
    bic = serializers.CharField(allow_blank=True)
    iban = serializers.CharField()
    currency = serializers.CharField()
    is_primary = serializers.BooleanField()
    is_active = serializers.BooleanField()
    created_at = serializers.CharField(allow_null=True)


class PartnerBankAccountWriteSerializer(serializers.Serializer):
    bank_name = serializers.CharField(required=False)
    bic = serializers.CharField(required=False)
    iban = serializers.CharField(required=False)
    currency = serializers.CharField(required=False)
    is_primary = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)


class PartnerBankAccountListSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    count = serializers.IntegerField()
    results = PartnerBankAccountSerializer(many=True)
