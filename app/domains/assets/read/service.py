"""Tenant-scoped Assets read queries — bounded aggregates, deterministic order."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import DecimalField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404

from accounting.models import DepreciationSchedule, FixedAsset, FixedAssetJournalLink, JournalEntryLine
from accounting.reporting.query import entry_audit_kind
from domains.assets.read.dto import (
    CAPITALIZATION_ROLES,
    DISPLAY_ROLE_ACTIVATION,
    DISPLAY_ROLE_DEPRECIATION,
    DISPLAY_ROLE_DISPOSAL,
    DISPLAY_ROLE_PURCHASE,
    asset_journal_entry_dto,
    capitalization_reconciliation_dto,
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


def _is_net_posted(entry) -> bool:
    return entry.status == 'posted' and entry.reversed_entry_id is None


def list_asset_journal_entries(tenant, asset_id: int) -> dict:
    asset = (
        FixedAsset.all_objects.filter(tenant=tenant, pk=asset_id)
        .select_related(
            'purchase_journal_entry',
            'activation_journal_entry',
            'disposal_journal_entry',
            'construction_account',
        )
        .prefetch_related(
            Prefetch(
                'journal_links',
                queryset=FixedAssetJournalLink.all_objects.select_related('journal_entry'),
            ),
            Prefetch(
                'depreciation_schedules',
                queryset=DepreciationSchedule.all_objects.select_related('journal_entry'),
            ),
        )
        .first()
    )
    if asset is None:
        raise Http404()

    composed: list[tuple] = []
    if asset.purchase_journal_entry_id:
        composed.append((asset.purchase_journal_entry, DISPLAY_ROLE_PURCHASE))
    if asset.activation_journal_entry_id:
        composed.append((asset.activation_journal_entry, DISPLAY_ROLE_ACTIVATION))
    if asset.disposal_journal_entry_id:
        composed.append((asset.disposal_journal_entry, DISPLAY_ROLE_DISPOSAL))
    for link in asset.journal_links.all():
        composed.append((link.journal_entry, link.role))
    for schedule in asset.depreciation_schedules.all():
        if schedule.journal_entry_id:
            composed.append((schedule.journal_entry, DISPLAY_ROLE_DEPRECIATION))

    je_ids = [entry.pk for entry, _role in composed]
    totals: dict[int, Decimal] = {pk: Decimal('0.00') for pk in je_ids}
    construction: dict[int, Decimal] = {pk: Decimal('0.00') for pk in je_ids}
    construction_id = asset.construction_account_id
    if je_ids:
        for journal_entry_id, account_id, debit, credit in JournalEntryLine.objects.filter(
            journal_entry_id__in=je_ids,
        ).values_list('journal_entry_id', 'account_id', 'debit_amount', 'credit_amount'):
            totals[journal_entry_id] += Decimal(debit)
            if account_id == construction_id:
                construction[journal_entry_id] += Decimal(debit) - Decimal(credit)

    composed.sort(key=lambda item: (item[0].entry_date, item[0].entry_number, item[0].pk))

    results = []
    capitalized_net = Decimal('0.00')
    for entry, role in composed:
        if role in CAPITALIZATION_ROLES:
            amount = construction[entry.pk] if _is_net_posted(entry) else Decimal('0.00')
            capitalized_net += amount
        else:
            amount = None
        results.append(
            asset_journal_entry_dto(
                entry=entry,
                role=role,
                total_amount=totals[entry.pk],
                capitalized_amount=amount,
                audit_kind=entry_audit_kind(entry),
            )
        )

    return {
        'results': results,
        'reconciliation': capitalization_reconciliation_dto(
            capitalized_net=capitalized_net,
            acquisition_cost=asset.acquisition_cost,
        ),
    }
