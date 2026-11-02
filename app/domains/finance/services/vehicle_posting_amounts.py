"""Iznosi splita službenog vozila. Omjeri dolaze iz VehicleTaxEvaluator."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from domains.tax.vehicle.contracts import (
    BenefitInKind,
    CitExceptionMode,
    DocumentFacts,
    EvidenceType,
    LineFacts,
    SupplierVatStatus,
    TaxEvaluationContext,
    VatExceptionMode,
    VehicleClass,
    VehicleFacts,
    VehicleLineKind,
)
from domains.tax.vehicle.evaluator import evaluate

CENT = Decimal('0.01')
ONE = Decimal('1')


def vehicle_split_amount(source, field: str) -> Decimal:
    amounts = _split(source)
    return amounts.get(field, Decimal('0.00'))


def passenger_split_ready(net: Decimal, vat: Decimal) -> bool:
    result = _evaluate(net, vat)
    return (
        result.vat_deductible_ratio is not None
        and result.cit_addback_ratio is not None
        and not result.requires_review
    )


def _split(source) -> dict[str, Decimal]:
    cached = getattr(source, '_vehicle_split_amounts', None)
    if cached is not None:
        return cached
    net = _money(getattr(source, 'amount', 0)) - _money(getattr(source, 'tax_amount', 0))
    vat = _money(getattr(source, 'tax_amount', 0))
    if net < 0:
        net = Decimal('0.00')
    result = _evaluate(net, vat)
    if (
        result.vat_deductible_ratio is None
        or result.cit_addback_ratio is None
        or result.requires_review
    ):
        amounts = {
            'vehicle_cost_recognized': Decimal('0.00'),
            'vehicle_cost_limited': Decimal('0.00'),
            'vehicle_vat_deductible': Decimal('0.00'),
        }
    else:
        recognized = _q(net * (ONE - result.cit_addback_ratio))
        deductible = _q(vat * result.vat_deductible_ratio)
        limited = (net - recognized) + (vat - deductible)
        amounts = {
            'vehicle_cost_recognized': recognized,
            'vehicle_cost_limited': limited,
            'vehicle_vat_deductible': deductible,
        }
    setattr(source, '_vehicle_split_amounts', amounts)
    return amounts


def _evaluate(net: Decimal, vat: Decimal):
    context = TaxEvaluationContext(
        line=LineFacts(
            vehicle_line_kind=VehicleLineKind.FUEL,
            net_amount=net,
            vat_amount=vat,
            gross_amount=net + vat,
        ),
        vehicle=VehicleFacts(
            vehicle_class=VehicleClass.M1,
            vat_exception_mode=VatExceptionMode.NONE,
            cit_exception_mode=CitExceptionMode.NONE,
            benefit_in_kind=BenefitInKind.NONE,
        ),
        document=DocumentFacts(
            evidence_type=EvidenceType.INVOICE,
            supplier_vat_status=SupplierVatStatus.REGISTERED,
        ),
    )
    return evaluate(context)


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
