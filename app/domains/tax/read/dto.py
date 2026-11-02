"""PDV Razdoblja list DTO — client identity is a period key, never VATPeriod.pk.

A calendar month is ``YYYY-MM``. A filing that crosses months is ``YYYY-MM..YYYY-MM``.
"""

from __future__ import annotations

import re
from decimal import Decimal

from accounting.models import VATPeriod
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes, compute_vat_due
from accounting.services.tax_forms.pdv.correction import correction_in_progress

_PERIOD_RE = re.compile(r'^(\d{4})-(0[1-9]|1[0-2])$')
_SPAN_RE = re.compile(r'^(\d{4})-(0[1-9]|1[0-2])\.\.(\d{4})-(0[1-9]|1[0-2])$')


def money2(value) -> str:
    return str(Decimal(value).quantize(Decimal('0.01')))


def period_key(period: VATPeriod) -> str:
    start, end = period.filing_bounds()
    if (start.year, start.month) == (end.year, end.month):
        return f'{period.year:04d}-{period.month:02d}'
    return f'{start.year:04d}-{start.month:02d}..{end.year:04d}-{end.month:02d}'


def parse_period_key(value: str) -> tuple[int, int] | None:
    match = _PERIOD_RE.fullmatch(value or '')
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def parse_span_key(value: str) -> tuple[int, int, int, int] | None:
    match = _SPAN_RE.fullmatch(value or '')
    if not match:
        return None
    return tuple(int(match.group(index)) for index in range(1, 5))


def pdv_s_submission_dto(event) -> dict:
    submitted_at = event.submitted_at
    return {
        'event_uuid': str(event.event_uuid),
        'submission_no': event.submission_no,
        'submission_type': event.submission_type,
        'external_identifier': str(event.external_identifier),
        'submitted_at': submitted_at.isoformat() if submitted_at is not None else None,
        'has_confirmation': bool(event.confirmation_attachment),
        'payload_hash': event.payload_hash,
    }


def pdv_period_dto(period: VATPeriod, *, has_ledger: bool) -> dict:
    vat_return = period.current_return
    latest = period.latest_return
    vat_due = compute_vat_due(aggregate_vat_boxes(period))
    submitted_at = period.submitted_at
    period_from, period_to = period.filing_bounds()
    snapshot = (vat_return.payload_snapshot or {}) if vat_return is not None else {}
    document_vat_due = snapshot.get('document_vat_due')
    document_currency = snapshot.get('document_currency') if document_vat_due is not None else None
    return {
        'period': period_key(period),
        'period_from': period_from.isoformat(),
        'period_to': period_to.isoformat(),
        'period_status': period.status,
        'has_ledger': bool(has_ledger),
        'return_version': vat_return.version if vat_return is not None else None,
        'return_status': vat_return.status if vat_return is not None else None,
        'return_source': vat_return.source if vat_return is not None else None,
        'latest_return_version': latest.version if latest is not None else None,
        'latest_return_status': latest.status if latest is not None else None,
        'correction_in_progress': correction_in_progress(period),
        'vat_due': money2(vat_due),
        'document_vat_due': document_vat_due,
        'document_currency': document_currency,
        'submitted_at': submitted_at.isoformat() if submitted_at is not None else None,
    }
