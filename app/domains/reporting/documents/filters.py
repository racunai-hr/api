"""Query-string filters and system views for the document list (ADR-0020)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

SYSTEM_VIEWS = frozenset({
    'attention',
    'unpaid_outgoing',
    'overdue_outgoing',
    'incoming_ready_to_pay',
    'partially_paid',
    'bank_unmatched',
    'unposted',
    'vat_pending',
    'vat_mismatch',
    'eracun_rejected',
    'possible_duplicates',
})

EXPORT_COLUMNS = (
    'direction',
    'id',
    'internal_number',
    'source_number',
    'partner_name',
    'partner_oib',
    'document_date',
    'due_date',
    'currency',
    'gross',
    'net',
    'vat',
    'document_status',
    'subledger_state',
    'open_amount',
    'vat_lifecycle',
    'operational_status',
    'controls',
)


@dataclass(frozen=True)
class DocumentListFilters:
    direction: str | None = None
    status: str | None = None
    search: str | None = None
    year: int | None = None
    month: int | None = None
    partner_id: int | None = None
    oib: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    currency: str | None = None
    view: str | None = None
    page: int = 1
    page_size: int = 20


def _int(raw: str | None) -> int | None:
    if raw in (None, ''):
        return None
    return int(raw)


def _dec(raw: str | None) -> Decimal | None:
    if raw in (None, ''):
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError('Neispravan iznos') from exc


def _date(raw: str | None) -> date | None:
    if raw in (None, ''):
        return None
    return date.fromisoformat(raw)


def parse_filters(query) -> DocumentListFilters:
    direction = query.get('direction') or None
    if direction not in (None, 'incoming', 'outgoing', 'deposit'):
        raise ValueError('direction mora biti incoming, outgoing ili deposit')
    view = query.get('view') or None
    if view and view not in SYSTEM_VIEWS:
        raise ValueError(f'Nepoznat view: {view}')
    page = max(1, _int(query.get('page')) or 1)
    page_size = _int(query.get('page_size')) or 20
    page_size = min(max(1, page_size), 100)
    year = _int(query.get('year'))
    month = _int(query.get('month'))
    partner_id = _int(query.get('partner'))
    return DocumentListFilters(
        direction=direction,
        status=query.get('status') or None,
        search=(query.get('search') or '').strip() or None,
        year=year,
        month=month,
        partner_id=partner_id,
        oib=(query.get('oib') or '').strip() or None,
        date_from=_date(query.get('date_from')),
        date_to=_date(query.get('date_to')),
        due_from=_date(query.get('due_from')),
        due_to=_date(query.get('due_to')),
        amount_min=_dec(query.get('amount_min')),
        amount_max=_dec(query.get('amount_max')),
        currency=(query.get('currency') or '').strip().upper() or None,
        view=view,
        page=page,
        page_size=page_size,
    )


def as_of_date(as_of: datetime) -> date:
    return as_of.date() if as_of.tzinfo is None else as_of.astimezone().date()
