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

ASSET_JOURNAL_ENTRY_KEYS = (
    'journal_entry_id',
    'entry_number',
    'entry_date',
    'description',
    'status',
    'audit_kind',
    'role',
    'total_amount',
    'capitalized_amount',
)

CAPITALIZATION_RECONCILIATION_KEYS = (
    'capitalized_net',
    'acquisition_cost',
    'difference',
    'balanced',
)

DISPLAY_ROLE_PURCHASE = 'purchase'
DISPLAY_ROLE_ACTIVATION = 'activation'
DISPLAY_ROLE_DISPOSAL = 'disposal'
DISPLAY_ROLE_DEPRECIATION = 'depreciation'

CAPITALIZATION_ROLES = frozenset({DISPLAY_ROLE_PURCHASE, 'dependent_cost'})


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


def asset_journal_entry_dto(
    *,
    entry,
    role: str,
    total_amount: Decimal,
    capitalized_amount: Decimal | None,
    audit_kind: str,
) -> dict:
    return {
        'journal_entry_id': entry.pk,
        'entry_number': entry.entry_number,
        'entry_date': _iso_date(entry.entry_date),
        'description': entry.description or '',
        'status': entry.status,
        'audit_kind': audit_kind,
        'role': role,
        'total_amount': money(total_amount),
        'capitalized_amount': money(capitalized_amount) if capitalized_amount is not None else None,
    }


def capitalization_reconciliation_dto(
    *,
    capitalized_net: Decimal,
    acquisition_cost: Decimal,
) -> dict:
    difference = capitalized_net - acquisition_cost
    return {
        'capitalized_net': money(capitalized_net),
        'acquisition_cost': money(acquisition_cost),
        'difference': money(difference),
        'balanced': difference == Decimal('0.00'),
    }
