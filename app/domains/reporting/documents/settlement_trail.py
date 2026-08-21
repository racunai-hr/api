"""Read-only settlement trail projection for incoming expense detail (ADR-0013 SSOT).

Classifies sibling JournalEntries by structured provenance when available; otherwise
legacy description markers via extract_document_type — heuristic only, never accounting SSOT.
"""

from __future__ import annotations

from decimal import Decimal

from accounting.services.journal_markers import extract_document_type
from domains.reporting.documents.projection import money


def _classify_posting_event(entry) -> str | None:
    """Return expense_approved / expense_paid / None.

    Prefer structured provenance if added later; v1 = legacy description marker only.
    """
    return extract_document_type(getattr(entry, 'description', None) or '')


def _je_debit_total(entry, lines_by_je: dict) -> Decimal:
    lines = lines_by_je.get(entry.pk, []) if lines_by_je else []
    total = Decimal('0')
    for line in lines:
        total += line.debit_amount or Decimal('0')
    return total


def _obligation_entry(sibling_jes: list):
    approved = [
        je
        for je in sibling_jes
        if je.status == 'posted' and _classify_posting_event(je) == 'expense_approved'
    ]
    if not approved:
        return None
    approved.sort(key=lambda je: (je.posted_at or je.created_at, je.pk))
    return approved[0]


def build_settlement_trail(
    *,
    subledger,
    allocations,
    sibling_jes: list,
    lines_by_je: dict | None = None,
    bank_by_je: dict | None = None,
    pfc_by_je: dict | None = None,
) -> dict:
    """Build settlement_trail block. SubledgerItem.open_amount is SSOT for open."""
    lines_by_je = lines_by_je or {}
    bank_by_je = bank_by_je or {}
    pfc_by_je = pfc_by_je or {}
    allocations = list(allocations or [])
    sibling_jes = list(sibling_jes or [])

    if subledger is None:
        return {
            'obligation': None,
            'closings': [],
            'system_entries': [],
            'warnings': [],
            'totals': {
                'obligation': None,
                'allocated': None,
                'open': None,
            },
        }

    obligation_je = _obligation_entry(sibling_jes)
    obligation = {
        'amount': money(subledger.original_amount),
        'journal_entry_id': obligation_je.pk if obligation_je else None,
        'entry_number': obligation_je.entry_number if obligation_je else None,
        'entry_date': (
            obligation_je.entry_date.isoformat()
            if obligation_je and obligation_je.entry_date
            else None
        ),
    }

    alloc_je_ids = {alloc.journal_entry_id for alloc in allocations if alloc.journal_entry_id}
    jes_by_id = {je.pk: je for je in sibling_jes}
    # Allocation JE may not be on expense GFK (unlikely) — still show closings from alloc rows.
    for alloc in allocations:
        je = getattr(alloc, 'journal_entry', None)
        if je is not None and je.pk not in jes_by_id:
            jes_by_id[je.pk] = je

    closings = []
    allocated_sum = Decimal('0')
    for alloc in sorted(allocations, key=lambda row: row.pk):
        allocated_sum += alloc.amount or Decimal('0')
        je = jes_by_id.get(alloc.journal_entry_id)
        bank_txs = bank_by_je.get(alloc.journal_entry_id, []) if alloc.journal_entry_id else []
        claims = pfc_by_je.get(alloc.journal_entry_id, []) if alloc.journal_entry_id else []

        if bank_txs:
            tx = bank_txs[0]
            statement = getattr(tx, 'bank_statement', None)
            closings.append(
                {
                    'kind': 'bank',
                    'amount': money(alloc.amount),
                    'journal_entry_id': alloc.journal_entry_id,
                    'entry_number': je.entry_number if je else None,
                    'allocation_id': alloc.pk,
                    'bank_transaction_id': tx.pk,
                    'bank_statement_id': statement.pk if statement is not None else tx.bank_statement_id,
                    'counterparty_name': (tx.counterparty_name or None) or None,
                    'private_funds_claim_id': None,
                    'claim_number': None,
                    'partner_id': None,
                    'partner_name': None,
                    'label': 'Banka',
                }
            )
        elif claims:
            claim = claims[0]
            partner = getattr(claim, 'partner', None)
            closings.append(
                {
                    'kind': 'private_funds',
                    'amount': money(alloc.amount),
                    'journal_entry_id': alloc.journal_entry_id,
                    'entry_number': je.entry_number if je else None,
                    'allocation_id': alloc.pk,
                    'bank_transaction_id': None,
                    'bank_statement_id': None,
                    'counterparty_name': None,
                    'private_funds_claim_id': claim.pk,
                    'claim_number': claim.number or None,
                    'partner_id': partner.pk if partner is not None else claim.partner_id,
                    'partner_name': partner.name if partner is not None else None,
                    'label': 'Privatna sredstva',
                }
            )
        else:
            closings.append(
                {
                    'kind': 'other',
                    'amount': money(alloc.amount),
                    'journal_entry_id': alloc.journal_entry_id,
                    'entry_number': je.entry_number if je else None,
                    'allocation_id': alloc.pk,
                    'bank_transaction_id': None,
                    'bank_statement_id': None,
                    'counterparty_name': None,
                    'private_funds_claim_id': None,
                    'claim_number': None,
                    'partner_id': None,
                    'partner_name': None,
                    'label': 'Ostalo',
                }
            )

    system_entries = []
    for je in sorted(sibling_jes, key=lambda row: row.pk):
        if je.pk in alloc_je_ids:
            continue
        if _classify_posting_event(je) != 'expense_paid':
            continue
        if je.status != 'posted':
            continue
        system_entries.append(
            {
                'kind': 'expense_paid',
                'amount': money(_je_debit_total(je, lines_by_je)),
                'journal_entry_id': je.pk,
                'entry_number': je.entry_number or None,
                'note': (
                    'Sistemski generirano (expense_paid); nije alokacija saldakonta.'
                ),
            }
        )

    warnings = []
    open_amount = subledger.open_amount
    if (
        system_entries
        and open_amount is not None
        and open_amount == 0
    ):
        warnings.append(
            {
                'code': 'possible_duplicate_expense_paid',
                'message': (
                    'Obveza je u cijelosti zatvorena alokacijama, a postoji i zasebno '
                    'sistemsko expense_paid knjiženje. Provjerite predstavlja li '
                    'dodatno knjiženje duplikat.'
                ),
            }
        )

    return {
        'obligation': obligation,
        'closings': closings,
        'system_entries': system_entries,
        'warnings': warnings,
        'totals': {
            'obligation': money(subledger.original_amount),
            'allocated': money(allocated_sum),
            'open': money(open_amount),
        },
    }
