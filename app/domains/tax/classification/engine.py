"""Pure TaxClassificationEngine. Does not read VATPeriod write permission."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from accounting.services.tax_forms.pdv.mapping import (
    PDV_MAPPING,
    PDV_MAPPING_VERSION,
    RC_INPUT_BOXES,
    invoice_eu_outbound_box,
    invoice_rate_to_box,
    is_eu_customer,
    is_eu_outbound_invoice_line,
    is_eu_supplier,
    journal_line_vat_rate,
    pretporez_base_from_vat,
    rc_output_box_for_input,
    rc_vat_from_base,
    viii1_box_amounts,
)
from accounting.services.tax_forms.pdv.supply_procedure import (
    VatSupplyProcedure,
    invoice_procedure_to_box,
)
from domains.tax.classification.accounts import is_tax_family_account, mapped_box_for_journal_line
from domains.tax.classification.contracts import (
    ClassifiedWithoutRowsError,
    Direction,
    EventKind,
    Outcome,
    PartnerProvenance,
    ProposedLedgerRow,
    TaxClassificationResult,
    TaxDocumentInput,
    TaxRelevance,
)

_ACTIVE_INVOICE = frozenset({'sent', 'paid', 'overdue'})
_ACTIVE_EXPENSE = frozenset({'approved', 'paid'})


def classify(document: TaxDocumentInput) -> TaxClassificationResult:
    """Classify one normalized input. Never inspects VATPeriod.status."""
    warnings: list[str] = []
    if document.partner and document.partner.provenance == PartnerProvenance.CURRENT_PARTNER:
        warnings.append('current_partner')

    if document.event_kind in {EventKind.REVERSAL, EventKind.CORRECTION}:
        return _classify_reversal(document, warnings)

    if document.source_kind in {'invoice', 'invoice_item'}:
        if document.lifecycle_status not in _ACTIVE_INVOICE:
            return _empty(document, Outcome.NOT_TAX_RELEVANT, 'inactive_invoice', warnings)
        return _classify_invoice(document, warnings)

    if document.source_kind == 'expense':
        if document.lifecycle_status not in _ACTIVE_EXPENSE:
            return _empty(document, Outcome.NOT_TAX_RELEVANT, 'inactive_expense', warnings)
        return _classify_expense(document, warnings)

    if document.source_kind == 'journal_line':
        if document.lifecycle_status != 'posted':
            return _empty(document, Outcome.NOT_TAX_RELEVANT, 'inactive_journal', warnings)
        return _classify_journal(document, warnings)

    return _empty(document, Outcome.INVALID, 'unknown_source_kind', warnings)


def _empty(
    document: TaxDocumentInput,
    outcome: Outcome,
    reason: str,
    warnings: list[str],
    *,
    rule_code: str = '',
) -> TaxClassificationResult:
    if outcome == Outcome.CLASSIFIED:
        raise ClassifiedWithoutRowsError(reason)
    return TaxClassificationResult(
        outcome=outcome,
        reason=reason,
        rule_code=rule_code or reason,
        rows=(),
        warnings=tuple(warnings),
        input_hash=document.input_hash,
        mapping_version=PDV_MAPPING_VERSION,
        period_year=document.period_year,
        period_month=document.period_month,
        period_date_reason='document_date',
        source_kind=document.source_kind,
        source_document_id=document.source_document_id,
        source_line_id=document.source_line_id,
    )


def _classified(
    document: TaxDocumentInput,
    rows: tuple[ProposedLedgerRow, ...],
    *,
    reason: str,
    rule_code: str,
    warnings: list[str],
) -> TaxClassificationResult:
    if not rows:
        raise ClassifiedWithoutRowsError(reason)
    return TaxClassificationResult(
        outcome=Outcome.CLASSIFIED,
        reason=reason,
        rule_code=rule_code,
        rows=rows,
        warnings=tuple(warnings),
        input_hash=document.input_hash,
        mapping_version=PDV_MAPPING_VERSION,
        period_year=document.period_year,
        period_month=document.period_month,
        period_date_reason='document_date',
        source_kind=document.source_kind,
        source_document_id=document.source_document_id,
        source_line_id=document.source_line_id,
    )


def _row(
    document: TaxDocumentInput,
    *,
    box: str,
    rule_code: str,
    base: Decimal,
    tax: Decimal,
    direction: Direction | None = None,
    sign: Decimal = Decimal('1'),
) -> ProposedLedgerRow:
    rule = PDV_MAPPING[box]
    direction = direction or (
        Direction.OUTPUT if rule.ledger_type == 'I-RA' else Direction.INPUT
    )
    recognized = tax if direction == Direction.INPUT and sign > 0 else Decimal('0.00')
    outputs: list[str] = ['pdv']
    if box in {'101', '103'}:
        outputs.append('zp')
    if box in {'207', '612'}:
        outputs.append('pdv_s')
    outputs.append('control_ira' if direction == Direction.OUTPUT else 'control_ura')
    return ProposedLedgerRow(
        source_kind=document.source_kind,
        source_document_id=document.source_document_id,
        source_line_id=document.source_line_id,
        event_kind=document.event_kind,
        box=box,
        category=rule.entry_category,
        direction=direction,
        ledger_type=rule.ledger_type,
        base_amount=base,
        tax_amount=tax,
        recognized_input_vat=recognized,
        unrecognized_input_vat=Decimal('0.00'),
        sign=sign,
        rule_code=rule_code,
        period_year=document.period_year,
        period_month=document.period_month,
        mapping_version=PDV_MAPPING_VERSION,
        outputs=tuple(outputs),
    )


def _classify_reversal(
    document: TaxDocumentInput,
    warnings: list[str],
) -> TaxClassificationResult:
    if document.tax_relevance == TaxRelevance.NOT_TAX_RELEVANT:
        return _empty(
            document,
            Outcome.NOT_TAX_RELEVANT,
            'reversal_not_tax_relevant',
            warnings,
            rule_code='REV_NOT_TAX_RELEVANT',
        )
    if document.tax_relevance == TaxRelevance.UNDETERMINED:
        return _empty(
            document,
            Outcome.REVIEW_REQUIRED,
            'reversal_tax_owner_undetermined',
            warnings,
            rule_code='REVERSAL_TAX_OWNER_UNDETERMINED',
        )
    if not document.originates_from:
        return _empty(document, Outcome.INVALID, 'reversal_missing_origin', warnings)
    if document.origin_effects_ambiguous:
        return _empty(
            document,
            Outcome.INVALID,
            'reversal_ambiguous_origin_effect',
            warnings,
            rule_code='REVERSAL_AMBIGUOUS_ORIGIN_EFFECT',
        )
    if not document.origin_tax_effects:
        return _empty(document, Outcome.INVALID, 'reversal_missing_ledger_evidence', warnings)

    rows = []
    for effect in document.origin_tax_effects:
        rows.append(
            _row(
                document,
                box=effect.box,
                rule_code='REV_INVERT',
                base=effect.base_amount,
                tax=effect.tax_amount,
                direction=effect.direction,
                sign=effect.sign * Decimal('-1'),
            )
        )
    return _classified(
        document,
        tuple(rows),
        reason='reversal_origin_tax_effect',
        rule_code='REV_INVERT',
        warnings=warnings,
    )


def _classify_invoice(
    document: TaxDocumentInput,
    warnings: list[str],
) -> TaxClassificationResult:
    if document.base_amount <= 0:
        return _empty(document, Outcome.NOT_TAX_RELEVANT, 'zero_invoice_base', warnings)

    procedure = document.declared_procedure or VatSupplyProcedure.STANDARD
    oss_box = invoice_procedure_to_box(procedure)
    if oss_box is not None:
        return _classified(
            document,
            (_row(
                document,
                box=oss_box,
                rule_code=f'INV_OSS_{oss_box}',
                base=document.base_amount,
                tax=document.vat_amount,
            ),),
            reason='oss_procedure',
            rule_code=f'INV_OSS_{oss_box}',
            warnings=warnings,
        )

    partner = document.partner
    rate = document.vat_rate if document.vat_rate is not None else Decimal('0.00')
    if partner is not None and is_eu_outbound_invoice_line(
        partner=_PartnerView(partner),
        tax_rate=rate,
    ):
        box = invoice_eu_outbound_box(has_service_date=bool(document.supply_date))
        return _classified(
            document,
            (_row(
                document,
                box=box,
                rule_code=f'INV_EU_{box}',
                base=document.base_amount,
                tax=Decimal('0.00'),
            ),),
            reason='eu_outbound',
            rule_code=f'INV_EU_{box}',
            warnings=warnings,
        )

    box = invoice_rate_to_box(rate)
    if box is None:
        if rate == 0 and partner is not None and is_eu_customer(_PartnerView(partner)):
            return _empty(document, Outcome.REVIEW_REQUIRED, 'eu_outbound_incomplete', warnings)
        return _empty(
            document,
            Outcome.REVIEW_REQUIRED,
            'unknown_invoice_rate',
            warnings,
            rule_code='UNKNOWN_RATE_NO_LONGER_SKIPPED',
        )
    return _classified(
        document,
        (_row(
            document,
            box=box,
            rule_code=f'INV_RATE_{box}',
            base=document.base_amount,
            tax=document.vat_amount,
        ),),
        reason='domestic_rate',
        rule_code=f'INV_RATE_{box}',
        warnings=warnings,
    )


def _classify_expense(
    document: TaxDocumentInput,
    warnings: list[str],
) -> TaxClassificationResult:
    procedure = document.declared_procedure or VatSupplyProcedure.STANDARD
    if procedure == VatSupplyProcedure.IOSS:
        net = document.base_amount
        if net <= 0 and not document.vat_amount:
            return _empty(document, Outcome.NOT_TAX_RELEVANT, 'zero_ioss_expense', warnings)
        return _classified(
            document,
            (_row(
                document,
                box='308',
                rule_code='EXP_IOSS_308',
                base=net,
                tax=document.vat_amount,
            ),),
            reason='ioss',
            rule_code='EXP_IOSS_308',
            warnings=warnings,
        )

    partner = document.partner
    if (
        partner is not None
        and is_eu_supplier(_PartnerView(partner))
        and not document.vat_amount
    ):
        if document.has_linked_journal_entry:
            return _empty(document, Outcome.NOT_TAX_RELEVANT, 'eu_expense_posted_via_journal', warnings)
        if document.base_amount > 0:
            return _classified(
                document,
                (_row(
                    document,
                    box='614',
                    rule_code='EXP_EU_614',
                    base=document.base_amount,
                    tax=Decimal('0.00'),
                ),),
                reason='eu_expense_placeholder',
                rule_code='EXP_EU_614',
                warnings=warnings,
            )

    if document.base_amount <= 0 and not document.vat_amount:
        return _empty(document, Outcome.NOT_TAX_RELEVANT, 'zero_expense', warnings)

    return _empty(
        document,
        Outcome.REVIEW_REQUIRED,
        'expense_generic_303_removed',
        warnings,
        rule_code='EXPENSE_GENERIC_303_REMOVED',
    )


def _classify_journal(
    document: TaxDocumentInput,
    warnings: list[str],
) -> TaxClassificationResult:
    account = document.account_code or ''
    if document.debit_amount and document.credit_amount:
        return _empty(document, Outcome.INVALID, 'journal_debit_and_credit', warnings)

    if account in {'0373'} and 'PPMV' in (document.description or '').upper():
        return _empty(document, Outcome.NOT_TAX_RELEVANT, 'ppmv_not_eu_acquisition', warnings)

    box = mapped_box_for_journal_line(
        account_code=account,
        debit_amount=document.debit_amount,
        credit_amount=document.credit_amount,
    )
    if box is not None:
        amount = document.debit_amount if document.debit_amount > 0 else document.credit_amount
        if box in {'611', '612', '613', '614', '615'}:
            base, vat = viii1_box_amounts(box, amount)
            rate = Decimal('0.00')
        elif box == '207' and account in {'0373'}:
            base, vat, rate = amount, Decimal('0.00'), Decimal('0.00')
        elif box in {'207'} and document.credit_amount > 0:
            rate = journal_line_vat_rate(account)
            vat = document.credit_amount
            base = Decimal('0.00') if box == '207' else pretporez_base_from_vat(vat, rate)
        elif document.debit_amount > 0:
            vat = document.debit_amount
            rate = journal_line_vat_rate(account)
            base = pretporez_base_from_vat(vat, rate)
        else:
            rate = journal_line_vat_rate(account)
            vat = document.credit_amount
            base = pretporez_base_from_vat(vat, rate)
        return _classified(
            document,
            (_row(document, box=box, rule_code=f'JE_{box}', base=base, tax=vat),),
            reason='journal_mapped',
            rule_code=f'JE_{box}',
            warnings=warnings,
        )

    if is_tax_family_account(account):
        return _empty(
            document,
            Outcome.REVIEW_REQUIRED,
            'unmapped_tax_family_account',
            warnings,
            rule_code='UNMAPPED_TAX_ACCOUNT_NO_LONGER_SKIPPED',
        )
    return _empty(document, Outcome.NOT_TAX_RELEVANT, 'non_tax_journal_account', warnings)


def reconcile_rc_bases(rows: tuple[ProposedLedgerRow, ...]) -> tuple[ProposedLedgerRow, ...]:
    """Align RC pretporez bases with paired acquisition 207/209/210 rows.

    Mirrors generate_vat_ledger _matched_rc_base without reading VATLedgerEntry, so
    inverse-from-VAT 0.01 drift (3970.59 → 15882.36 vs 0373 15882.35) is resolved
    from the classified asset row, not from eu_rc_base_from_vat which would also
    rewrite exact 2000.00 → 8000.00 pairs.
    """
    reconciled = []
    rate = Decimal('25.00')
    for row in rows:
        if row.box not in RC_INPUT_BOXES:
            reconciled.append(row)
            continue
        output_box = rc_output_box_for_input(row.box)
        if output_box is None:
            reconciled.append(row)
            continue
        candidate_bases = [
            other.base_amount
            for other in rows
            if other.box == output_box and other.base_amount > 0
        ]
        matched = [
            base for base in candidate_bases
            if rc_vat_from_base(base, rate) == row.tax_amount
        ]
        if len(matched) == 1:
            reconciled.append(replace(row, base_amount=matched[0]))
            continue
        if not matched and candidate_bases:
            total = sum(candidate_bases, Decimal('0.00'))
            if rc_vat_from_base(total, rate) == row.tax_amount:
                reconciled.append(replace(row, base_amount=total))
                continue
        reconciled.append(row)
    return tuple(reconciled)


class _PartnerView:
    """Minimal partner shape for mapping helpers (not an ORM model)."""

    def __init__(self, snapshot) -> None:
        self.tax_number = snapshot.tax_number
        self.vat_number = snapshot.vat_id
        self.country = snapshot.country
        self.name = snapshot.name
