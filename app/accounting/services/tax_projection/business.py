"""Gate E business multiset: parity without PK/origin."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from accounting.models import VATLedgerEntry, VATLedgerOrigin
from accounting.services.tax_projection.contracts import ProjectedLedgerRow, VatProjectionCandidate
from accounting.services.tax_projection.fingerprints import writable_rows

_TWO = Decimal('0.01')


def _money(value: Decimal | int | str | None) -> Decimal:
    return Decimal(value or 0).quantize(_TWO, rounding=ROUND_HALF_UP)


def _category(value) -> str:
    if value is None:
        return ''
    return str(getattr(value, 'value', value) or '')


def _rate(*, base: Decimal, tax: Decimal, stored: Decimal | None = None) -> Decimal:
    # Zero-base RC/output VAT lines must not treat a stored rate as a business identity
    # (legacy often stores 25.00 on tax-only 207; candidate leaves rate unset → 0.00).
    if base == 0:
        return Decimal('0.00')
    if stored is not None:
        return _money(stored)
    return _money(tax / base * Decimal('100'))


def business_key_from_amounts(
    *,
    box: str,
    base_amount: Decimal,
    tax_amount: Decimal,
    vat_rate: Decimal | None = None,
    category: str = '',
) -> tuple:
    base = _money(base_amount)
    tax = _money(tax_amount)
    rate = _rate(base=abs(base) if base != 0 else Decimal('0'), tax=abs(tax), stored=vat_rate)
    return (
        box or '',
        format(base, 'f'),
        format(tax, 'f'),
        format(rate, 'f'),
        _category(category),
    )


def business_key_from_row(row: ProjectedLedgerRow) -> tuple:
    signed_base = row.base_amount * row.sign
    signed_tax = row.tax_amount * row.sign
    return business_key_from_amounts(
        box=row.box,
        base_amount=signed_base,
        tax_amount=signed_tax,
        vat_rate=None,
        category=row.category,
    )


def business_key_from_entry(entry: VATLedgerEntry) -> tuple:
    return business_key_from_amounts(
        box=entry.vat_box or '',
        base_amount=entry.base_amount,
        tax_amount=entry.vat_amount,
        vat_rate=entry.vat_rate,
        category=entry.entry_category or '',
    )


def business_multiset_from_rows(rows: Iterable[ProjectedLedgerRow]) -> Counter:
    return Counter(business_key_from_row(row) for row in writable_rows(tuple(rows)))


def business_multiset_from_candidate(candidate: VatProjectionCandidate) -> Counter:
    return business_multiset_from_rows(candidate.rows)


def business_multiset_from_entries(entries: Iterable[VATLedgerEntry]) -> Counter:
    return Counter(
        business_key_from_entry(entry)
        for entry in entries
        if entry.origin != VATLedgerOrigin.MANUAL and not entry.is_manual
    )


def business_multiset_from_ledger(period, *, origins: tuple[str, ...] | None = None) -> Counter:
    qs = VATLedgerEntry.all_objects.filter(vat_period=period).exclude(
        origin=VATLedgerOrigin.MANUAL,
    ).exclude(is_manual=True)
    if origins is not None:
        qs = qs.filter(origin__in=origins)
    return business_multiset_from_entries(qs)


def business_multisets_equal(left: Counter, right: Counter) -> bool:
    return left == right


def assert_business_multisets_equal(left: Counter, right: Counter) -> None:
    if left != right:
        raise ValueError('LEGACY_CANDIDATE_MISMATCH')


def count_box(multiset: Counter, box: str) -> int:
    return sum(count for key, count in multiset.items() if key[0] == box)
