"""ORM adapters → TaxDocumentInput. Finance layer; local model imports like vat.py."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from accounting.models import JournalEntry, JournalEntryLine, VATLedgerEntry, VATPeriod
from accounting.services.tax_forms.pdv.mapping import partner_eu_vat_id
from accounting.services.tax_forms.pdv.supply_procedure import VatSupplyProcedure
from domains.tax.classification.contracts import (
    Direction,
    EventKind,
    OriginTaxEffect,
    PartnerProvenance,
    PartnerSnapshot,
    TaxDocumentInput,
)
from domains.tax.classification.hashing import hash_tax_input


def _finalize(document: TaxDocumentInput) -> TaxDocumentInput:
    hashed = replace(document, input_hash='')
    return replace(hashed, input_hash=hash_tax_input(hashed))


def _partner_snapshot(partner, *, provenance: PartnerProvenance) -> PartnerSnapshot | None:
    if partner is None:
        return None
    vat_id = partner_eu_vat_id(partner)
    return PartnerSnapshot(
        name=partner.name or '',
        country=partner.country or '',
        tax_number=getattr(partner, 'tax_number', '') or '',
        vat_id=vat_id,
        provenance=provenance,
    )


def adapt_invoice_item(item, *, period: VATPeriod) -> TaxDocumentInput:
    invoice = item.invoice
    base = (item.quantity or 0) * (item.unit_price or 0)
    rate = item.tax_rate or Decimal('0')
    vat = base * rate / Decimal('100')
    procedure = getattr(item, 'vat_procedure', VatSupplyProcedure.STANDARD) or VatSupplyProcedure.STANDARD
    return _finalize(
        TaxDocumentInput(
            tenant_id=invoice.tenant_id,
            source_kind='invoice_item',
            source_document_id=invoice.pk,
            source_line_id=item.pk,
            lifecycle_status=invoice.status,
            event_kind=EventKind.ORIGINAL,
            direction=Direction.OUTPUT,
            document_date=invoice.issue_date,
            supply_date=invoice.service_date,
            partner=_partner_snapshot(invoice.company_to, provenance=PartnerProvenance.CURRENT_PARTNER),
            base_amount=base,
            vat_rate=rate,
            vat_amount=vat,
            currency='EUR',
            jurisdiction='',
            customer_type='B2B',
            supply_kind='services' if invoice.service_date else 'goods',
            declared_procedure=procedure,
            originates_from=None,
            origin_tax_effects=(),
            origin_effects_ambiguous=False,
            has_linked_journal_entry=False,
            account_code=None,
            debit_amount=Decimal('0.00'),
            credit_amount=Decimal('0.00'),
            description=item.item_name or '',
            period_year=period.year,
            period_month=period.month,
            input_hash='',
        )
    )


def adapt_invoice_header(invoice, *, period: VATPeriod) -> TaxDocumentInput:
    return _finalize(
        TaxDocumentInput(
            tenant_id=invoice.tenant_id,
            source_kind='invoice',
            source_document_id=invoice.pk,
            source_line_id=None,
            lifecycle_status=invoice.status,
            event_kind=EventKind.ORIGINAL,
            direction=Direction.OUTPUT,
            document_date=invoice.issue_date,
            supply_date=invoice.service_date,
            partner=_partner_snapshot(invoice.company_to, provenance=PartnerProvenance.CURRENT_PARTNER),
            base_amount=invoice.subtotal or Decimal('0.00'),
            vat_rate=Decimal('25.00') if invoice.tax_amount else Decimal('0.00'),
            vat_amount=invoice.tax_amount or Decimal('0.00'),
            currency='EUR',
            jurisdiction='',
            customer_type='B2B',
            supply_kind='services' if invoice.service_date else 'goods',
            declared_procedure=VatSupplyProcedure.STANDARD,
            originates_from=None,
            origin_tax_effects=(),
            origin_effects_ambiguous=False,
            has_linked_journal_entry=False,
            account_code=None,
            debit_amount=Decimal('0.00'),
            credit_amount=Decimal('0.00'),
            description=invoice.description or '',
            period_year=period.year,
            period_month=period.month,
            input_hash='',
        )
    )


def adapt_expense(expense, *, period: VATPeriod) -> TaxDocumentInput:
    expense_ct = ContentType.objects.get_for_model(type(expense))
    has_je = JournalEntry.all_objects.filter(
        tenant_id=expense.tenant_id,
        source_content_type=expense_ct,
        source_object_id=expense.pk,
    ).exists()
    net = Decimal(expense.amount or 0) - Decimal(expense.tax_amount or 0)
    procedure = getattr(expense, 'vat_procedure', VatSupplyProcedure.STANDARD) or VatSupplyProcedure.STANDARD
    return _finalize(
        TaxDocumentInput(
            tenant_id=expense.tenant_id,
            source_kind='expense',
            source_document_id=expense.pk,
            source_line_id=None,
            lifecycle_status=expense.status,
            event_kind=EventKind.ORIGINAL,
            direction=Direction.INPUT,
            document_date=expense.expense_date,
            supply_date=None,
            partner=_partner_snapshot(expense.supplier, provenance=PartnerProvenance.CURRENT_PARTNER),
            base_amount=net,
            vat_rate=None,
            vat_amount=Decimal(expense.tax_amount or 0),
            currency=expense.currency or 'EUR',
            jurisdiction='',
            customer_type='B2B',
            supply_kind='unknown',
            declared_procedure=procedure,
            originates_from=None,
            origin_tax_effects=(),
            origin_effects_ambiguous=False,
            has_linked_journal_entry=has_je,
            account_code=None,
            debit_amount=Decimal('0.00'),
            credit_amount=Decimal('0.00'),
            description=expense.description or '',
            period_year=period.year,
            period_month=period.month,
            input_hash='',
        )
    )


def _effects_from_ledger(entries) -> tuple[OriginTaxEffect, ...]:
    effects = []
    for entry in entries:
        direction = Direction.OUTPUT if entry.ledger_type == VATLedgerEntry.LEDGER_I_RA else Direction.INPUT
        effects.append(
            OriginTaxEffect(
                box=entry.vat_box,
                base_amount=entry.base_amount,
                tax_amount=entry.vat_amount,
                direction=direction,
                category=entry.entry_category,
                ledger_type=entry.ledger_type,
                sign=Decimal('1'),
                source_kind='journal_line',
                source_document_id=entry.source_object_id or 0,
                source_line_id=entry.source_object_id,
                ledger_entry_id=entry.pk,
            )
        )
    return tuple(effects)


def origin_tax_effects_for_reversal(reversal: JournalEntry, *, period: VATPeriod) -> tuple[tuple[OriginTaxEffect, ...], bool]:
    original = reversal.reversed_entry
    if original is None:
        return (), False
    line_ct = ContentType.objects.get_for_model(JournalEntryLine)
    line_ids = list(original.lines.values_list('pk', flat=True))
    rows = list(
        VATLedgerEntry.all_objects.filter(
            tenant_id=reversal.tenant_id,
            source_content_type=line_ct,
            source_object_id__in=line_ids,
        )
    )
    if not rows:
        return (), False
    boxes = {row.vat_box for row in rows}
    amounts = {(row.vat_box, row.base_amount, row.vat_amount) for row in rows}
    ambiguous = len(rows) > 1 and len(amounts) != len(rows)
    # Multiple rows are OK when they are distinct box/amount pairs (RC). Ambiguous if duplicates without line identity.
    if len(rows) > 1 and len(boxes) == 1 and len(amounts) < len(rows) and any(not row.source_object_id for row in rows):
        ambiguous = True
    return _effects_from_ledger(rows), ambiguous


def adapt_reversal_entry(entry: JournalEntry, *, period: VATPeriod) -> TaxDocumentInput:
    """One classification input per reversal JE, not per swapped D/C line."""
    line = entry.lines.select_related('account').first()
    if line is None:
        line = JournalEntryLine(journal_entry=entry, debit_amount=Decimal('0'), credit_amount=Decimal('0'))
        line.pk = 0
    document = adapt_journal_line(line, period=period)
    return document


def adapt_journal_line(line: JournalEntryLine, *, period: VATPeriod) -> TaxDocumentInput:
    entry = line.journal_entry
    event_kind = EventKind.REVERSAL if entry.reversed_entry_id else EventKind.ORIGINAL
    direction = Direction.CORRECTIVE if event_kind == EventKind.REVERSAL else Direction.INPUT
    originates = None
    effects: tuple[OriginTaxEffect, ...] = ()
    ambiguous = False
    if event_kind == EventKind.REVERSAL:
        originates = f'journal_entry:{entry.reversed_entry_id}'
        effects, ambiguous = origin_tax_effects_for_reversal(entry, period=period)
        direction = Direction.CORRECTIVE
    return _finalize(
        TaxDocumentInput(
            tenant_id=entry.tenant_id,
            source_kind='journal_line',
            source_document_id=entry.pk,
            source_line_id=line.pk,
            lifecycle_status=entry.status,
            event_kind=event_kind,
            direction=direction,
            document_date=entry.entry_date,
            supply_date=None,
            partner=None,
            base_amount=Decimal('0.00'),
            vat_rate=None,
            vat_amount=Decimal('0.00'),
            currency='EUR',
            jurisdiction='RH',
            customer_type='unknown',
            supply_kind='unknown',
            declared_procedure='standard',
            originates_from=originates,
            origin_tax_effects=effects,
            origin_effects_ambiguous=ambiguous,
            has_linked_journal_entry=False,
            account_code=line.account.account_code if line.account_id else None,
            debit_amount=line.debit_amount or Decimal('0.00'),
            credit_amount=line.credit_amount or Decimal('0.00'),
            description=entry.description or '',
            period_year=period.year,
            period_month=period.month,
            input_hash='',
        )
    )
