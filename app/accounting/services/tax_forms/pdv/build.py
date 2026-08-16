"""Build PdvPayload from a VATPeriod — single aggregate call only."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable

from accounting.models import VATPeriod
from accounting.services.tax_forms.pdv.aggregate import BoxTotals, aggregate_vat_boxes, compute_vat_due
from accounting.services.tax_forms.pdv.boxes import BoxValueSource, VATBoxDefinition, active_boxes
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING_VERSION
from accounting.services.tax_forms.pdv.payload import (
    PDV_SCHEMA_VERSION,
    PdvFieldPair,
    PdvMarginPair,
    PdvPayload,
    TaxpayerInfo,
    freeze_fields,
)
from accounting.services.tax_forms.pdv.supply_procedure import ECOMMERCE_EXCLUDED_FROM_VAT_DUE
from settings.models import CompanySettings, ResponsiblePerson

_CATEGORY_TOTAL_EXCLUDE = frozenset({'200', '300'})


@dataclass(frozen=True)
class _BuildContext:
    boxes: dict[str, BoxTotals]
    output_total: BoxTotals
    input_total: BoxTotals


def _period_bounds(period: VATPeriod) -> tuple[date, date]:
    last_day = calendar.monthrange(period.year, period.month)[1]
    return date(period.year, period.month, 1), date(period.year, period.month, last_day)


def _taxpayer_for_period(period: VATPeriod) -> TaxpayerInfo:
    settings = CompanySettings.all_objects.filter(tenant=period.tenant).first()
    if settings is None:
        raise ValueError(f'CompanySettings missing for tenant {period.tenant_id}')
    return TaxpayerInfo(
        oib=settings.vat_number or '',
        name=settings.company_name,
        street=settings.street,
        house_number=settings.house_number,
        city=settings.city,
        postal_code=settings.postal_code,
    )


def _sum_category_pairs(boxes: dict[str, BoxTotals], category: str) -> BoxTotals:
    base = Decimal('0.00')
    vat = Decimal('0.00')
    for definition in active_boxes():
        if definition.field_type != 'pair':
            continue
        if definition.category != category:
            continue
        if definition.code in _CATEGORY_TOTAL_EXCLUDE:
            continue
        if definition.code in ECOMMERCE_EXCLUDED_FROM_VAT_DUE:
            continue
        totals = boxes[definition.code]
        base += totals.base
        vat += totals.vat
    return BoxTotals(base.quantize(Decimal('0.01')), vat.quantize(Decimal('0.01')))


def _no_output_activity(boxes: dict[str, BoxTotals]) -> bool:
    for definition in active_boxes():
        if definition.field_type != 'pair':
            continue
        if definition.category != 'output' or definition.code == '200':
            continue
        totals = boxes[definition.code]
        if totals.base != Decimal('0.00') or totals.vat != Decimal('0.00'):
            return False
    return True


def _build_pair(definition: VATBoxDefinition, ctx: _BuildContext) -> PdvFieldPair:
    totals = ctx.boxes[definition.code]
    return PdvFieldPair(totals.base, totals.vat)


def _build_margin_pair(definition: VATBoxDefinition, ctx: _BuildContext) -> PdvMarginPair:
    totals = ctx.boxes[definition.code]
    return PdvMarginPair(totals.base, totals.vat)


def _build_category_total_output(_definition: VATBoxDefinition, ctx: _BuildContext) -> PdvFieldPair:
    return PdvFieldPair(ctx.output_total.base, ctx.output_total.vat)


def _build_category_total_input(_definition: VATBoxDefinition, ctx: _BuildContext) -> PdvFieldPair:
    return PdvFieldPair(ctx.input_total.base, ctx.input_total.vat)


def _build_aggregate_base(definition: VATBoxDefinition, ctx: _BuildContext) -> Decimal:
    return ctx.boxes[definition.code].base


def _build_aggregate_vat(definition: VATBoxDefinition, ctx: _BuildContext) -> Decimal:
    return ctx.boxes[definition.code].vat


def _build_computed_vat_due(_definition: VATBoxDefinition, ctx: _BuildContext) -> Decimal:
    return compute_vat_due(ctx.boxes)


def _build_boolean_no_output(_definition: VATBoxDefinition, ctx: _BuildContext) -> bool:
    return _no_output_activity(ctx.boxes)


def _build_zero(definition: VATBoxDefinition, _ctx: _BuildContext) -> Decimal | bool:
    if definition.field_type == 'bool':
        return False
    return Decimal('0.00')


_VALUE_SOURCE_HANDLERS: dict[
    BoxValueSource,
    Callable[[VATBoxDefinition, _BuildContext], PdvFieldPair | PdvMarginPair | Decimal | bool],
] = {
    BoxValueSource.PAIR: _build_pair,
    BoxValueSource.MARGIN_PAIR: _build_margin_pair,
    BoxValueSource.CATEGORY_TOTAL_OUTPUT: _build_category_total_output,
    BoxValueSource.CATEGORY_TOTAL_INPUT: _build_category_total_input,
    BoxValueSource.AGGREGATE_BASE: _build_aggregate_base,
    BoxValueSource.AGGREGATE_VAT: _build_aggregate_vat,
    BoxValueSource.COMPUTED_VAT_DUE: _build_computed_vat_due,
    BoxValueSource.BOOLEAN_NO_OUTPUT: _build_boolean_no_output,
    BoxValueSource.ZERO: _build_zero,
}


def map_boxes_to_pdv_fields(boxes: dict[str, BoxTotals]) -> dict[str, PdvFieldPair | PdvMarginPair | Decimal | bool]:
    """Transform aggregate box totals into PDV form field values."""
    ctx = _BuildContext(
        boxes=boxes,
        output_total=_sum_category_pairs(boxes, 'output'),
        input_total=_sum_category_pairs(boxes, 'input'),
    )
    fields: dict[str, PdvFieldPair | PdvMarginPair | Decimal | bool] = {}
    for definition in active_boxes():
        handler = _VALUE_SOURCE_HANDLERS[definition.value_source]
        fields[definition.code] = handler(definition, ctx)
    return fields


def build_pdv_payload(period: VATPeriod) -> PdvPayload:
    boxes = aggregate_vat_boxes(period)
    fields = map_boxes_to_pdv_fields(boxes)
    period_from, period_to = _period_bounds(period)
    return PdvPayload(
        schema_version=PDV_SCHEMA_VERSION,
        mapping_version=PDV_MAPPING_VERSION,
        period_from=period_from,
        period_to=period_to,
        taxpayer=_taxpayer_for_period(period),
        fields=freeze_fields(fields),
    )


def accountant_for_tenant(tenant) -> ResponsiblePerson | None:
    settings = CompanySettings.all_objects.filter(tenant=tenant).first()
    if settings is None:
        return None
    return (
        settings.responsible_persons.filter(title='accountant', is_active=True).first()
        or settings.responsible_persons.filter(is_active=True).order_by('-is_primary').first()
    )
