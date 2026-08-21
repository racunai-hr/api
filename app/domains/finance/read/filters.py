"""Query filters and pagination for journal entry read API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from accounting.models import JournalEntry

JOURNAL_STATUSES = frozenset(dict(JournalEntry.STATUS_CHOICES))


def _int(raw: str | None) -> int | None:
    if raw in (None, ''):
        return None
    return int(raw)


def _date(raw: str | None) -> date | None:
    if raw in (None, ''):
        return None
    return date.fromisoformat(raw)


def parse_page(query) -> tuple[int, int]:
    page = max(1, _int(query.get('page')) or 1)
    page_size = _int(query.get('page_size')) or 20
    page_size = min(max(1, page_size), 100)
    return page, page_size


@dataclass(frozen=True)
class JournalEntryListFilters:
    status: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    search: str = ''
    page: int = 1
    page_size: int = 20


def parse_journal_entry_filters(query) -> JournalEntryListFilters:
    status = (query.get('status') or '').strip() or None
    if status and status not in JOURNAL_STATUSES:
        raise ValueError('status mora biti draft, posted ili reversed')
    page, page_size = parse_page(query)
    return JournalEntryListFilters(
        status=status,
        date_from=_date(query.get('date_from')),
        date_to=_date(query.get('date_to')),
        search=(query.get('search') or '').strip(),
        page=page,
        page_size=page_size,
    )
