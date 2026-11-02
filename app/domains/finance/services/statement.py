"""Partner chronological statement / kartica (Finance domain, Faza 2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.utils import timezone

from accounting.models import JournalEntryLine, PrivateFundsClaim, SubledgerAllocation, SubledgerItem
from banking.models import BankTransaction
from domains.finance.services.aging import _money_str, _subledger_source_label

DIRECTION_ALL = 'all'
DIRECTION_RECEIVABLE = 'receivable'
DIRECTION_PAYABLE = 'payable'
VALID_DIRECTIONS = frozenset({DIRECTION_ALL, DIRECTION_RECEIVABLE, DIRECTION_PAYABLE})

SOURCE_TYPE_LABELS = {
    'invoice': 'Izlazni račun',
    'expense': 'Ulazni trošak',
    'deposit': 'Kaucija',
    'privatefundsclaim': 'Privatna sredstva',
}

KIND_OPENING = 'opening_balance'
KIND_OBLIGATION = 'obligation'
KIND_ALLOCATION = 'allocation'


@dataclass(frozen=True)
class _LedgerEvent:
    kind: str
    event_date: date
    direction: str
    debit: Decimal
    credit: Decimal
    journal_entry_id: int
    journal_entry_pk: int
    subledger_item_id: int | None = None
    allocation_id: int | None = None
    source_type: str | None = None
    source_id: int | None = None
    source_label: str | None = None
    document_type_label: str | None = None
    closing_kind: str | None = None
    entry_number: str | None = None


def _normalize_direction(value: str | None) -> str:
    if value in VALID_DIRECTIONS:
        return value
    return DIRECTION_ALL


def _event_debit_credit(direction: str, *, obligation: bool, amount: Decimal) -> tuple[Decimal, Decimal]:
    """Map obligation/allocation amounts to D/K columns (ZAKLJUČANO)."""
    if direction == 'receivable':
        if obligation:
            return amount, Decimal('0')
        return Decimal('0'), amount
    # payable
    if obligation:
        return Decimal('0'), amount
    return amount, Decimal('0')


def _balance_to_debit_credit(balance: Decimal) -> tuple[Decimal, Decimal]:
    """Opening row display: positive → Duguje, negative → Potražuje."""
    if balance > 0:
        return balance, Decimal('0')
    if balance < 0:
        return Decimal('0'), abs(balance)
    return Decimal('0'), Decimal('0')


def _document_type_label(source_type: str | None) -> str | None:
    if not source_type:
        return None
    return SOURCE_TYPE_LABELS.get(source_type, source_type)


def _account_base_code(account_code: str | None) -> str:
    if not account_code:
        return ''
    return account_code.split('-', 1)[0]


def _prepaid_offset_je_ids(tenant, journal_entry_ids: list[int]) -> set[int]:
    """Allocation JEs that credit prepaid asset 1909 (structured GL, not JE text)."""
    if not journal_entry_ids:
        return set()
    prepaid: set[int] = set()
    lines = JournalEntryLine.objects.filter(
        journal_entry_id__in=journal_entry_ids,
        journal_entry__tenant=tenant,
    ).select_related('account')
    for line in lines:
        if _account_base_code(line.account.account_code) != '1909':
            continue
        if (line.credit_amount or Decimal('0')) > Decimal('0'):
            prepaid.add(line.journal_entry_id)
    return prepaid


def _classify_closing_kind(
    journal_entry_id: int,
    *,
    bank_je_ids: set[int],
    pfc_je_ids: set[int],
    prepaid_je_ids: set[int],
) -> str:
    if journal_entry_id in bank_je_ids:
        return 'bank'
    if journal_entry_id in pfc_je_ids:
        return 'private_funds'
    if journal_entry_id in prepaid_je_ids:
        return 'prepaid'
    return 'other'


def _partner_items_qs(tenant, partner_id: int):
    return (
        SubledgerItem.all_objects.filter(tenant=tenant, partner_id=partner_id)
        .exclude(status='cancelled')
        .select_related('journal_entry', 'source_content_type')
        .prefetch_related('allocations__journal_entry')
    )


def _collect_events(tenant, partner_id: int) -> list[_LedgerEvent]:
    items = list(_partner_items_qs(tenant, partner_id))
    allocation_je_ids = [
        alloc.journal_entry_id
        for item in items
        for alloc in item.allocations.all()
        if alloc.journal_entry_id
    ]
    bank_je_ids = set(
        BankTransaction.all_objects.filter(
            tenant=tenant,
            matched_journal_entry_id__in=allocation_je_ids,
        ).values_list('matched_journal_entry_id', flat=True)
    ) if allocation_je_ids else set()
    pfc_je_ids = set(
        PrivateFundsClaim.all_objects.filter(
            tenant=tenant,
            journal_entry_id__in=allocation_je_ids,
        ).values_list('journal_entry_id', flat=True)
    ) if allocation_je_ids else set()
    prepaid_je_ids = _prepaid_offset_je_ids(tenant, allocation_je_ids)

    events: list[_LedgerEvent] = []
    for item in items:
        obligation_je = item.journal_entry
        if obligation_je is None or obligation_je.entry_date is None:
            continue
        debit, credit = _event_debit_credit(
            item.direction,
            obligation=True,
            amount=item.original_amount,
        )
        source_type = item.source_content_type.model
        events.append(
            _LedgerEvent(
                kind=KIND_OBLIGATION,
                event_date=obligation_je.entry_date,
                direction=item.direction,
                debit=debit,
                credit=credit,
                journal_entry_id=obligation_je.pk,
                journal_entry_pk=obligation_je.pk,
                subledger_item_id=item.pk,
                source_type=source_type,
                source_id=item.source_object_id,
                source_label=_subledger_source_label(item),
                document_type_label=_document_type_label(source_type),
                entry_number=obligation_je.entry_number,
            )
        )
        for alloc in item.allocations.all():
            alloc_je = alloc.journal_entry
            if alloc_je is None or alloc_je.entry_date is None:
                continue
            alloc_debit, alloc_credit = _event_debit_credit(
                item.direction,
                obligation=False,
                amount=alloc.amount,
            )
            events.append(
                _LedgerEvent(
                    kind=KIND_ALLOCATION,
                    event_date=alloc_je.entry_date,
                    direction=item.direction,
                    debit=alloc_debit,
                    credit=alloc_credit,
                    journal_entry_id=alloc_je.pk,
                    journal_entry_pk=alloc_je.pk,
                    subledger_item_id=item.pk,
                    allocation_id=alloc.pk,
                    source_type=source_type,
                    source_id=item.source_object_id,
                    source_label=_subledger_source_label(item),
                    document_type_label=_document_type_label(source_type),
                    closing_kind=_classify_closing_kind(
                        alloc_je.pk,
                        bank_je_ids=bank_je_ids,
                        pfc_je_ids=pfc_je_ids,
                        prepaid_je_ids=prepaid_je_ids,
                    ),
                    entry_number=alloc_je.entry_number,
                )
            )
    events.sort(key=lambda e: (e.event_date, e.journal_entry_pk))
    return events


def _filter_events_by_direction(events: list[_LedgerEvent], direction: str) -> list[_LedgerEvent]:
    if direction == DIRECTION_ALL:
        return events
    return [e for e in events if e.direction == direction]


def _compute_balance(events: list[_LedgerEvent]) -> Decimal:
    balance = Decimal('0')
    for event in events:
        balance += event.debit - event.credit
    return balance


def _opening_balance_before(events: list[_LedgerEvent], year: int) -> Decimal:
    cutoff = date(year, 1, 1)
    prior = [e for e in events if e.event_date < cutoff]
    return _compute_balance(prior)


def _year_has_events(events: list[_LedgerEvent], year: int) -> bool:
    return any(e.event_date.year == year for e in events)


def available_years(
    tenant,
    partner_id: int,
    *,
    direction: str = DIRECTION_ALL,
) -> list[int]:
    direction = _normalize_direction(direction)
    all_events = _collect_events(tenant, partner_id)
    filtered = _filter_events_by_direction(all_events, direction)
    current_year = timezone.localdate().year

    years: set[int] = set()
    for event in filtered:
        years.add(event.event_date.year)

    if filtered:
        min_year = min(e.event_date.year for e in filtered)
    else:
        min_year = current_year

    for year in range(min_year, current_year + 1):
        opening = _opening_balance_before(filtered, year)
        if opening != Decimal('0'):
            years.add(year)

    return sorted(years)


def resolve_year(
    tenant,
    partner_id: int,
    requested_year: int | None,
    *,
    direction: str = DIRECTION_ALL,
) -> int:
    years = available_years(tenant, partner_id, direction=direction)
    current_year = timezone.localdate().year
    if not years:
        return requested_year if requested_year is not None else current_year
    if requested_year is not None and requested_year in years:
        return requested_year
    if requested_year is None and current_year in years:
        return current_year
    if requested_year is None:
        return max(years)
    return max(years)


def _serialize_amounts(debit: Decimal, credit: Decimal, balance: Decimal) -> dict:
    return {
        'debit': _money_str(debit),
        'credit': _money_str(credit),
        'balance': _money_str(balance),
    }


def _serialize_event_row(event: _LedgerEvent, balance: Decimal) -> dict:
    row = {
        'kind': event.kind,
        'date': event.event_date.isoformat(),
        'direction': event.direction,
        'debit': _money_str(event.debit),
        'credit': _money_str(event.credit),
        'balance': _money_str(balance),
        'journal_entry_id': event.journal_entry_id,
    }
    if event.entry_number:
        row['entry_number'] = event.entry_number
    if event.subledger_item_id is not None:
        row['subledger_item_id'] = event.subledger_item_id
    if event.allocation_id is not None:
        row['allocation_id'] = event.allocation_id
    if event.source_type:
        row['source_type'] = event.source_type
    if event.source_id is not None:
        row['source_id'] = event.source_id
    if event.source_label:
        row['source_label'] = event.source_label
    if event.document_type_label:
        row['document_type_label'] = event.document_type_label
    if event.closing_kind:
        row['closing_kind'] = event.closing_kind
    return row


def partner_statement(
    tenant,
    partner_id: int,
    *,
    year: int | None = None,
    direction: str = DIRECTION_ALL,
    currency: str = 'EUR',
) -> dict:
    direction = _normalize_direction(direction)
    resolved_year = resolve_year(tenant, partner_id, year, direction=direction)
    all_events = _collect_events(tenant, partner_id)
    filtered_events = _filter_events_by_direction(all_events, direction)

    opening_balance_value = _opening_balance_before(filtered_events, resolved_year)
    opening_debit, opening_credit = _balance_to_debit_credit(opening_balance_value)

    rows: list[dict] = []
    running = opening_balance_value

    if opening_balance_value != Decimal('0') or _year_has_events(filtered_events, resolved_year):
        rows.append({
            'kind': KIND_OPENING,
            'date': date(resolved_year, 1, 1).isoformat(),
            'label': f'Početno stanje {resolved_year}',
            'debit': _money_str(opening_debit),
            'credit': _money_str(opening_credit),
            'balance': _money_str(running),
        })

    year_start = date(resolved_year, 1, 1)
    year_end = date(resolved_year, 12, 31)
    year_events = [
        e for e in filtered_events if year_start <= e.event_date <= year_end
    ]

    for event in year_events:
        running += event.debit - event.credit
        rows.append(_serialize_event_row(event, running))

    total_debit = opening_debit + sum((e.debit for e in year_events), Decimal('0'))
    total_credit = opening_credit + sum((e.credit for e in year_events), Decimal('0'))

    return {
        'partner_id': partner_id,
        'year': resolved_year,
        'available_years': available_years(tenant, partner_id, direction=direction),
        'currency': currency,
        'direction': direction,
        'opening_balance': _serialize_amounts(opening_debit, opening_credit, opening_balance_value),
        'rows': rows,
        'closing_balance': _serialize_amounts(total_debit, total_credit, running),
    }
