"""Partner aging i open-item izvještaji (Finance domain)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.utils import timezone

from accounting.models import SubledgerItem

AGING_BUCKET_KEYS = ('current', '1_30', '31_60', '61_90', '90_plus')
AGING_BUCKET_LABELS = {
    'current': 'Nedospjelo',
    '1_30': '1–30 dana',
    '31_60': '31–60 dana',
    '61_90': '61–90 dana',
    '90_plus': '90+ dana',
}


def _empty_aging_buckets() -> dict[str, Decimal]:
    return {key: Decimal('0') for key in AGING_BUCKET_KEYS}


def aging_bucket_for_days(days_overdue: int) -> str:
    """Mapira broj dana kašnjenja u aging bucket."""
    if days_overdue <= 0:
        return 'current'
    if days_overdue <= 30:
        return '1_30'
    if days_overdue <= 60:
        return '31_60'
    if days_overdue <= 90:
        return '61_90'
    return '90_plus'


def _open_subledger_items_qs(tenant, *, direction: str | None = None):
    qs = SubledgerItem.all_objects.filter(
        tenant=tenant,
        status__in=('open', 'partial'),
    ).exclude(open_amount__lte=Decimal('0')).select_related('partner', 'source_content_type')
    if direction:
        qs = qs.filter(direction=direction)
    return qs.order_by('partner__name', 'due_date', 'id')


def _subledger_source_label(item: SubledgerItem) -> str:
    source = item.source
    if source is None:
        return f'{item.source_content_type.model}#{item.source_object_id}'
    for attr in ('invoice_number', 'expense_number'):
        value = getattr(source, attr, None)
        if value:
            return str(value)
    return str(source)


def subledger_open_items(
    tenant,
    *,
    direction: str | None = None,
    as_of_date: date | None = None,
) -> list[dict]:
    """Lista otvorenih AR/AP stavki — partner, dokument, dospijeće, otvoreni iznos."""
    if as_of_date is None:
        as_of_date = timezone.localdate()

    items = []
    for item in _open_subledger_items_qs(tenant, direction=direction):
        days_overdue = (as_of_date - item.due_date).days
        items.append({
            'item_id': item.pk,
            'partner_id': item.partner_id,
            'partner_name': item.partner.name,
            'direction': item.direction,
            'direction_label': item.get_direction_display(),
            'source_type': item.source_content_type.model,
            'source_id': item.source_object_id,
            'source_label': _subledger_source_label(item),
            'original_amount': item.original_amount,
            'open_amount': item.open_amount,
            'due_date': item.due_date,
            'days_overdue': days_overdue,
            'aging_bucket': aging_bucket_for_days(days_overdue),
            'status': item.status,
        })
    return items


def get_partner_aging(
    tenant,
    *,
    direction: str | None = None,
    as_of_date: date | None = None,
) -> dict:
    """Partner aging — zbroj otvorenih iznosa po bucketima (current, 1–30, …, 90+)."""
    if as_of_date is None:
        as_of_date = timezone.localdate()

    partner_rows: dict[tuple[int, str], dict] = {}
    totals = _empty_aging_buckets()
    totals['total'] = Decimal('0')

    for row in subledger_open_items(tenant, direction=direction, as_of_date=as_of_date):
        row_key = (row['partner_id'], row['direction'])
        if row_key not in partner_rows:
            partner_rows[row_key] = {
                'partner_id': row['partner_id'],
                'partner_name': row['partner_name'],
                'direction': row['direction'],
                'direction_label': row['direction_label'],
                'buckets': _empty_aging_buckets(),
                'total': Decimal('0'),
            }
        entry = partner_rows[row_key]
        bucket = row['aging_bucket']
        amount = row['open_amount']
        entry['buckets'][bucket] += amount
        entry['total'] += amount
        totals[bucket] += amount
        totals['total'] += amount

    return {
        'as_of_date': as_of_date,
        'direction': direction,
        'partners': sorted(
            partner_rows.values(),
            key=lambda r: (r['partner_name'].lower(), r['direction']),
        ),
        'totals': totals,
    }


# Backward-compatible alias used by legacy reporting code.
partner_aging = get_partner_aging
