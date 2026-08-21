"""DTO builders for journal entry read API — allowlisted fields only."""

from __future__ import annotations

from decimal import Decimal

SOURCE_TYPE_INVOICE = 'invoice'
SOURCE_TYPE_EXPENSE = 'expense'
SOURCE_TYPE_MANUAL = 'manual'
SOURCE_TYPE_ASSET = 'asset'
SOURCE_TYPE_OTHER = 'other'

SOURCE_TYPES = (
    SOURCE_TYPE_INVOICE,
    SOURCE_TYPE_EXPENSE,
    SOURCE_TYPE_MANUAL,
    SOURCE_TYPE_ASSET,
    SOURCE_TYPE_OTHER,
)

_GFK_SOURCE_TYPE = {
    'invoice': SOURCE_TYPE_INVOICE,
    'expense': SOURCE_TYPE_EXPENSE,
    'fixedasset': SOURCE_TYPE_ASSET,
}


def money(value: Decimal | None) -> str:
    if value is None:
        return '0.00'
    return f'{Decimal(value):.2f}'


def journal_source_type(entry) -> str:
    """Derive source from JournalEntry GFK and is_auto only — never bank match."""
    ct = getattr(entry, 'source_content_type', None)
    if ct is not None:
        return _GFK_SOURCE_TYPE.get(ct.model, SOURCE_TYPE_OTHER)
    if not entry.is_auto:
        return SOURCE_TYPE_MANUAL
    return SOURCE_TYPE_OTHER


def journal_entry_list_dto(entry) -> dict:
    return {
        'id': entry.pk,
        'entry_number': entry.entry_number,
        'entry_date': entry.entry_date.isoformat() if entry.entry_date else None,
        'description': entry.description or '',
        'status': entry.status,
        'is_auto': bool(entry.is_auto),
        'source_type': journal_source_type(entry),
        'total_debit': money(getattr(entry, 'total_debit_sum', None)),
        'total_credit': money(getattr(entry, 'total_credit_sum', None)),
    }


def journal_entry_line_dto(line) -> dict:
    account = line.account
    return {
        'id': line.pk,
        'account_code': account.account_code if account is not None else '',
        'account_name': account.account_name if account is not None else '',
        'description': line.description or '',
        'debit': money(line.debit_amount),
        'credit': money(line.credit_amount),
    }


def journal_entry_detail_dto(entry, *, as_of: str) -> dict:
    """Detail reuses list fields + source_type helper — do not re-derive elsewhere."""
    payload = journal_entry_list_dto(entry)
    lines = sorted(entry.lines.all(), key=lambda row: row.pk)
    payload.update(
        {
            'as_of': as_of,
            'reference': entry.reference or '',
            'source_id': entry.source_object_id,
            'lines': [journal_entry_line_dto(line) for line in lines],
        }
    )
    return payload
