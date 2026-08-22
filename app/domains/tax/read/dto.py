"""PDV Razdoblja list DTO — client identity is YYYY-MM, never VATPeriod.pk."""

from __future__ import annotations

from decimal import Decimal

from accounting.models import VATPeriod
from accounting.services.vat import aggregate_vat_period


def money2(value) -> str:
    return str(Decimal(value).quantize(Decimal('0.01')))


def period_key(period: VATPeriod) -> str:
    return f'{period.year:04d}-{period.month:02d}'


def pdv_period_dto(period: VATPeriod, *, has_ledger: bool) -> dict:
    vat_return = period.current_return
    vat_due = aggregate_vat_period(period)['vat_due']
    submitted_at = period.submitted_at
    return {
        'period': period_key(period),
        'period_status': period.status,
        'has_ledger': bool(has_ledger),
        'return_version': vat_return.version if vat_return is not None else None,
        'return_status': vat_return.status if vat_return is not None else None,
        'vat_due': money2(vat_due),
        'submitted_at': submitted_at.isoformat() if submitted_at is not None else None,
    }
