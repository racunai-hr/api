"""Aggregate VAT ledger entries by VATBox."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from accounting.models import VATLedgerEntry
from accounting.services.tax_forms.pdv.boxes import active_boxes
from accounting.services.tax_forms.pdv.supply_procedure import ECOMMERCE_EXCLUDED_FROM_VAT_DUE


@dataclass(frozen=True)
class BoxTotals:
    base: Decimal
    vat: Decimal


def aggregate_vat_boxes(period) -> dict[str, BoxTotals]:
    """GROUP BY vat_box, then zero-fill all active boxes from registry."""
    raw = (
        VATLedgerEntry.all_objects.filter(vat_period=period)
        .exclude(vat_box='')
        .values('vat_box')
        .annotate(base=Sum('base_amount'), vat=Sum('vat_amount'))
    )
    result = {box.code: BoxTotals(Decimal('0.00'), Decimal('0.00')) for box in active_boxes()}
    for row in raw:
        result[row['vat_box']] = BoxTotals(
            base=row['base'] or Decimal('0.00'),
            vat=row['vat'] or Decimal('0.00'),
        )
    return result


def compute_vat_due(boxes: dict[str, BoxTotals]) -> Decimal:
    """Podatak400: output VAT minus input VAT (detail boxes only, not 200/300 totals)."""
    output_vat = Decimal('0.00')
    input_vat = Decimal('0.00')
    for definition in active_boxes():
        if definition.field_type != 'pair':
            continue
        totals = boxes[definition.code]
        if definition.category == 'output' and definition.code not in ('200', *ECOMMERCE_EXCLUDED_FROM_VAT_DUE):
            output_vat += totals.vat
        elif definition.category == 'input' and definition.code not in ('300', *ECOMMERCE_EXCLUDED_FROM_VAT_DUE):
            input_vat += totals.vat
    return (output_vat - input_vat).quantize(Decimal('0.01'))
