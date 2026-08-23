"""OpenAPI schema for the Assets read API — allowlisted fields only."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers

from accounting.models import FixedAssetOrigin, FixedAssetStatus
from config.schema_common import money_field


class FixedAssetListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    inventory_number = serializers.CharField(allow_blank=True)
    name = serializers.CharField()
    status = serializers.CharField()
    origin = serializers.CharField()
    purchase_date = serializers.DateField(allow_null=True)
    activation_date = serializers.DateField(allow_null=True)
    acquisition_cost = money_field()
    accumulated_depreciation = money_field()
    current_book_value = money_field()


class FixedAssetDetailSerializer(FixedAssetListItemSerializer):
    vin = serializers.CharField(allow_blank=True)
    useful_life_months = serializers.IntegerField(allow_null=True)
    depreciation_method = serializers.CharField()
    activation_journal_entry_id = serializers.IntegerField(allow_null=True)


class PaginatedFixedAssetsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = FixedAssetListItemSerializer(many=True)


class DepreciationScheduleItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    depreciation_amount = money_field()
    accumulated_depreciation = money_field()
    book_value_after = money_field()
    posted = serializers.BooleanField()
    journal_entry_id = serializers.IntegerField(allow_null=True)


class DepreciationScheduleListSerializer(serializers.Serializer):
    results = DepreciationScheduleItemSerializer(many=True)


FIXED_ASSET_LIST_PARAMS = [
    OpenApiParameter(
        'status',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        enum=list(FixedAssetStatus.values),
    ),
    OpenApiParameter(
        'origin',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        enum=list(FixedAssetOrigin.values),
    ),
    OpenApiParameter(
        'search',
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        description='Filter by name, VIN, or inventory number',
    ),
    OpenApiParameter(
        name='page',
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        description='Page number (min 1, default 1)',
    ),
]
