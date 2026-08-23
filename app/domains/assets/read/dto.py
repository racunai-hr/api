"""DTO builders for the Assets read API — allowlisted fields only."""

from __future__ import annotations

from decimal import Decimal

from accounting.models import DepreciationSchedule, FixedAsset

LIST_ITEM_KEYS = (
    'id',
    'inventory_number',
    'name',
    'status',
    'origin',
    'purchase_date',
    'activation_date',
    'acquisition_cost',
    'accumulated_depreciation',
    'current_book_value',
)

DETAIL_KEYS = LIST_ITEM_KEYS + (
    'vin',
    'useful_life_months',
    'depreciation_method',
    'activation_journal_entry_id',
)

SCHEDULE_ITEM_KEYS = (
    'id',
    'year',
    'month',
    'depreciation_amount',
    'accumulated_depreciation',
    'book_value_after',
    'posted',
    'journal_entry_id',
)


def money(value: Decimal | None) -> str:
    if value is None:
        return '0.00'
    return f'{Decimal(value):.2f}'


def _iso_date(value) -> str | None:
    return value.isoformat() if value else None


def _accumulated(asset: FixedAsset) -> Decimal:
    annotated = getattr(asset, 'accumulated_posted', None)
    if annotated is not None:
        return Decimal(annotated)
    return asset.accumulated_depreciation


def fixed_asset_list_item_dto(asset: FixedAsset) -> dict:
    accumulated = _accumulated(asset)
    return {
        'id': asset.pk,
        'inventory_number': asset.inventory_number or '',
        'name': asset.name,
        'status': asset.status,
        'origin': asset.origin,
        'purchase_date': _iso_date(asset.purchase_date),
        'activation_date': _iso_date(asset.in_service_date),
        'acquisition_cost': money(asset.acquisition_cost),
        'accumulated_depreciation': money(accumulated),
        'current_book_value': money(asset.acquisition_cost - accumulated),
    }


def fixed_asset_detail_dto(asset: FixedAsset) -> dict:
    payload = fixed_asset_list_item_dto(asset)
    payload.update(
        {
            'vin': asset.vin or '',
            'useful_life_months': asset.useful_life_months,
            'depreciation_method': asset.depreciation_method,
            'activation_journal_entry_id': asset.activation_journal_entry_id,
        }
    )
    return payload


def _row_posted(row: DepreciationSchedule) -> bool:
    entry = row.journal_entry
    return bool(row.journal_entry_id and entry is not None and entry.status == 'posted')


def depreciation_schedule_item_dtos(
    asset: FixedAsset,
    rows: list[DepreciationSchedule],
) -> list[dict]:
    running_all = Decimal('0.00')
    running_posted = Decimal('0.00')
    items: list[dict] = []
    for row in rows:
        amount = Decimal(row.amount)
        running_all += amount
        if _row_posted(row):
            running_posted += amount
        items.append(
            {
                'id': row.pk,
                'year': row.year,
                'month': row.month,
                'depreciation_amount': money(amount),
                'accumulated_depreciation': money(running_posted),
                'book_value_after': money(asset.acquisition_cost - running_all),
                'posted': _row_posted(row),
                'journal_entry_id': row.journal_entry_id,
            }
        )
    return items
