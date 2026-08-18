"""Pure period finalization — derived aggregates such as box 610."""

from __future__ import annotations

from decimal import Decimal

from accounting.services.tax_forms.pdv.mapping import (
    PDV_MAPPING,
    PDV_MAPPING_VERSION,
    VIII1_SUB_BOXES,
    VIII1_VAT_AGGREGATE_BOXES,
)
from domains.tax.classification.contracts import (
    Direction,
    EventKind,
    ProposedLedgerRow,
)


def finalize_period(
    rows: tuple[ProposedLedgerRow, ...],
    *,
    period_year: int,
    period_month: int,
    period_id: int = 0,
) -> tuple[ProposedLedgerRow, ...]:
    """Return classified rows plus derived period rows. Box 610 is created only here."""
    total = Decimal('0.00')
    for row in rows:
        if row.box not in VIII1_SUB_BOXES:
            continue
        amount = row.tax_amount if row.box in VIII1_VAT_AGGREGATE_BOXES else row.base_amount
        total += amount * row.sign

    if total == 0:
        return rows

    rule = PDV_MAPPING['610']
    derived = ProposedLedgerRow(
        source_kind='vat_period',
        source_document_id=period_id,
        source_line_id=None,
        event_kind=EventKind.ORIGINAL,
        box='610',
        category=rule.entry_category,
        direction=Direction.INPUT,
        ledger_type=rule.ledger_type,
        base_amount=abs(total),
        tax_amount=Decimal('0.00'),
        recognized_input_vat=Decimal('0.00'),
        unrecognized_input_vat=Decimal('0.00'),
        sign=Decimal('1') if total >= 0 else Decimal('-1'),
        rule_code='PERIOD_VIII1_610',
        period_year=period_year,
        period_month=period_month,
        mapping_version=PDV_MAPPING_VERSION,
        outputs=('pdv', 'control_ura'),
    )
    return rows + (derived,)
