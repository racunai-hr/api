"""Tenant-scoped Assets read queries — bounded aggregates, deterministic order."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404

from accounting.models import DepreciationSchedule, FixedAsset
from domains.assets.read.dto import (
    depreciation_schedule_item_dtos,
    fixed_asset_detail_dto,
    fixed_asset_list_item_dto,
)
from domains.assets.read.filters import FixedAssetListFilters

_MONEY = DecimalField(max_digits=15, decimal_places=2)
_ZERO = Value(Decimal('0.00'), output_field=_MONEY)


def _accumulated_annotation():
    return Coalesce(
        Sum(
            'depreciation_schedules__amount',
            filter=Q(depreciation_schedules__journal_entry__status='posted'),
        ),
        _ZERO,
    )


def _asset_queryset(tenant):
    return (
        FixedAsset.all_objects.filter(tenant=tenant)
        .annotate(accumulated_posted=_accumulated_annotation())
        .order_by('-purchase_date', 'name', 'pk')
    )


def list_fixed_assets(tenant, filters: FixedAssetListFilters) -> dict:
    qs = _asset_queryset(tenant)
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.origin:
        qs = qs.filter(origin=filters.origin)
    if filters.search:
        term = filters.search
        qs = qs.filter(
            Q(name__icontains=term)
            | Q(vin__icontains=term)
            | Q(inventory_number__icontains=term)
        )
    total = qs.count()
    start = (filters.page - 1) * filters.page_size
    page_rows = list(qs[start : start + filters.page_size])
    return {
        'count': total,
        'page': filters.page,
        'page_size': filters.page_size,
        'results': [fixed_asset_list_item_dto(row) for row in page_rows],
    }


def get_fixed_asset(tenant, asset_id: int) -> dict:
    asset = _asset_queryset(tenant).filter(pk=asset_id).first()
    if asset is None:
        raise Http404()
    return fixed_asset_detail_dto(asset)


def list_depreciation_schedule(tenant, asset_id: int) -> dict:
    asset = FixedAsset.all_objects.filter(tenant=tenant, pk=asset_id).first()
    if asset is None:
        raise Http404()
    rows = list(
        DepreciationSchedule.all_objects.filter(tenant=tenant, fixed_asset=asset)
        .select_related('journal_entry')
        .order_by('year', 'month', 'pk')
    )
    return {'results': depreciation_schedule_item_dtos(asset, rows)}
