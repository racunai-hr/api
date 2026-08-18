"""Normalized Decimal multiset diff between shadow rows and VATLedgerEntry."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from domains.tax.classification.contracts import ProposedLedgerRow, TaxClassificationResult, Outcome

_TWO_PLACES = Decimal('0.01')


def _money(value: Decimal) -> Decimal:
    return Decimal(value or 0).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class MatchKey:
    source_kind: str
    source_document_id: int
    event_kind: str
    box: str
    direction: str
    base_amount: Decimal
    tax_amount: Decimal
    recognized_input_vat: Decimal
    unrecognized_input_vat: Decimal
    sign: Decimal

    def as_tuple(self) -> tuple:
        return (
            self.source_kind,
            self.source_document_id,
            self.event_kind,
            self.box,
            self.direction,
            format(self.base_amount, 'f'),
            format(self.tax_amount, 'f'),
            format(self.recognized_input_vat, 'f'),
            format(self.unrecognized_input_vat, 'f'),
            format(self.sign, 'f'),
        )


@dataclass(frozen=True)
class DiffRecord:
    kind: str
    key: MatchKey
    shadow_count: int
    ledger_count: int
    source_kind: str
    source_document_id: int
    outcome: str


def row_match_key(row: ProposedLedgerRow) -> MatchKey:
    return MatchKey(
        source_kind=_legacy_source_kind(row.source_kind),
        source_document_id=row.source_document_id,
        event_kind=row.event_kind.value,
        box=row.box,
        direction=row.direction.value,
        base_amount=_money(row.base_amount),
        tax_amount=_money(row.tax_amount),
        recognized_input_vat=_money(row.recognized_input_vat),
        unrecognized_input_vat=_money(row.unrecognized_input_vat),
        sign=_money(row.sign),
    )


def _legacy_source_kind(kind: str) -> str:
    if kind in {'invoice', 'invoice_item'}:
        return 'invoice'
    return kind


def ledger_match_key(entry) -> MatchKey:
    model = ''
    if entry.source_content_type_id:
        model = entry.source_content_type.model
    source_kind = {
        'invoice': 'invoice',
        'expense': 'expense',
        'journalentryline': 'journal_line',
        'vatperiod': 'vat_period',
    }.get(model, model or 'unknown')
    document_id = entry.source_object_id or 0
    if source_kind == 'journal_line' and entry.source_object_id:
        from accounting.models import JournalEntryLine

        line = JournalEntryLine.objects.filter(pk=entry.source_object_id).select_related('journal_entry').first()
        if line is not None:
            document_id = line.journal_entry_id
    direction = 'output' if entry.ledger_type == 'I-RA' else 'input'
    return MatchKey(
        source_kind=source_kind,
        source_document_id=document_id,
        event_kind='original',
        box=entry.vat_box or '',
        direction=direction,
        base_amount=_money(entry.base_amount),
        tax_amount=_money(entry.vat_amount),
        recognized_input_vat=_money(entry.vat_amount if direction == 'input' else Decimal('0.00')),
        unrecognized_input_vat=Decimal('0.00'),
        sign=_money(Decimal('1')),
    )


def diff_multisets(
    *,
    results: list[TaxClassificationResult],
    finalized_rows: tuple[ProposedLedgerRow, ...],
    ledger_entries,
) -> tuple[list[DiffRecord], list[TaxClassificationResult]]:
    shadow_keys = Counter(row_match_key(row).as_tuple() for row in finalized_rows if row.box)
    ledger_keys = Counter(ledger_match_key(entry).as_tuple() for entry in ledger_entries if entry.vat_box)
    records: list[DiffRecord] = []
    all_keys = set(shadow_keys) | set(ledger_keys)
    key_objects = {row_match_key(row).as_tuple(): row_match_key(row) for row in finalized_rows}
    for entry in ledger_entries:
        key_objects.setdefault(ledger_match_key(entry).as_tuple(), ledger_match_key(entry))

    for raw in sorted(all_keys):
        shadow_count = shadow_keys.get(raw, 0)
        ledger_count = ledger_keys.get(raw, 0)
        if shadow_count == ledger_count:
            continue
        key = key_objects[raw]
        if shadow_count == 0:
            kind = 'extra_row'
        elif ledger_count == 0:
            kind = 'missing_row'
        elif shadow_count != ledger_count:
            kind = 'multiplicity_mismatch'
        else:
            kind = 'amount_mismatch'
        records.append(
            DiffRecord(
                kind=kind,
                key=key,
                shadow_count=shadow_count,
                ledger_count=ledger_count,
                source_kind=key.source_kind,
                source_document_id=key.source_document_id,
                outcome='',
            )
        )

    review_sources = [
        result
        for result in results
        if result.outcome in {Outcome.REVIEW_REQUIRED, Outcome.INVALID, Outcome.NOT_TAX_RELEVANT}
    ]
    return records, review_sources
