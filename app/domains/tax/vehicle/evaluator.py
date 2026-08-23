"""Pure VehicleTaxEvaluator. Prepared context in, TaxEvaluationResult out. No I/O."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from domains.tax.vehicle.contracts import (
    BenefitInKind,
    CitExceptionMode,
    CitTreatment,
    LineRuleKind,
    ReasonCode,
    TaxEvaluationContext,
    TaxEvaluationResult,
    VatExceptionMode,
    VatTreatment,
    VehicleClass,
    VehicleLineKind,
)
from domains.tax.vehicle.rules import AxisRule, rule_for

ZERO = Decimal('0.00')
HALF = Decimal('0.50')
ONE = Decimal('1.00')


@dataclass(frozen=True)
class _VatAxis:
    treatment: VatTreatment | None
    ratio: Decimal | None
    requires_review: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class _CitAxis:
    treatment: CitTreatment | None
    ratio: Decimal | None
    requires_review: bool
    reasons: tuple[str, ...]


def evaluate(context: TaxEvaluationContext) -> TaxEvaluationResult:
    """P0–P4 over a prepared context. Does not read ORM or write state."""
    vat = _evaluate_vat(context)
    cit = _evaluate_cit(context)
    return TaxEvaluationResult(
        vat_treatment=vat.treatment,
        vat_deductible_ratio=vat.ratio,
        vat_requires_review=vat.requires_review,
        cit_treatment=cit.treatment,
        cit_addback_ratio=cit.ratio,
        cit_requires_review=cit.requires_review,
        reason_codes=vat.reasons + cit.reasons,
        requires_review=vat.requires_review or cit.requires_review,
    )


def _kind_value(context: TaxEvaluationContext) -> str | None:
    kind = context.line.vehicle_line_kind
    if kind is None:
        return None
    return str(kind)


def _evaluate_vat(context: TaxEvaluationContext) -> _VatAxis:
    raw = _kind_value(context)
    if raw is None:
        return _vat_review(ReasonCode.VAT_LINE_NOT_CLASSIFIED)
    if raw == VehicleLineKind.UNCLASSIFIED:
        return _vat_review(ReasonCode.VAT_LINE_UNCLASSIFIED)
    rules = rule_for(raw)
    if rules is None:
        return _vat_review(ReasonCode.VAT_KIND_UNKNOWN)
    return _vat_from_rule(context, rules.vat)


def _evaluate_cit(context: TaxEvaluationContext) -> _CitAxis:
    raw = _kind_value(context)
    if raw is None:
        return _cit_review(ReasonCode.CIT_LINE_NOT_CLASSIFIED)
    if raw == VehicleLineKind.UNCLASSIFIED:
        return _cit_review(ReasonCode.CIT_LINE_UNCLASSIFIED)
    rules = rule_for(raw)
    if rules is None:
        return _cit_review(ReasonCode.CIT_KIND_UNKNOWN)
    return _cit_from_rule(context, rules.cit)


def _vat_from_rule(context: TaxEvaluationContext, rule: AxisRule) -> _VatAxis:
    if rule.rule_kind == LineRuleKind.REVIEW:
        return _vat_review(rule.reason or ReasonCode.VAT_KIND_UNKNOWN)
    if rule.rule_kind == LineRuleKind.HARD_EXPECTED:
        return _vat_hard_expected(context, rule)
    return _vat_candidate(context)


def _cit_from_rule(context: TaxEvaluationContext, rule: AxisRule) -> _CitAxis:
    if rule.rule_kind == LineRuleKind.REVIEW:
        return _cit_review(rule.reason or ReasonCode.CIT_TREATMENT_UNRESOLVED)
    if rule.rule_kind == LineRuleKind.HARD_EXPECTED:
        return _cit_hard_expected(rule)
    return _cit_candidate(context)


def _vat_hard_expected(context: TaxEvaluationContext, rule: AxisRule) -> _VatAxis:
    if context.line.vat_amount > 0:
        return _vat_review(ReasonCode.VAT_EVIDENCE_CONTRADICTION)
    return _VatAxis(
        treatment=VatTreatment.NO_INPUT_VAT,
        ratio=ZERO,
        requires_review=False,
        reasons=(rule.reason or ReasonCode.VAT_NO_INPUT_TAX,),
    )


def _vat_candidate(context: TaxEvaluationContext) -> _VatAxis:
    if context.line.vat_amount <= 0:
        return _vat_review(ReasonCode.VAT_EVIDENCE_INSUFFICIENT)
    vehicle = context.vehicle
    if vehicle.vehicle_class is None:
        return _vat_review(ReasonCode.VAT_CLASS_UNRESOLVED)
    if vehicle.vehicle_class == VehicleClass.OTHER:
        return _vat_review(ReasonCode.VAT_CLASS_UNRESOLVED)
    if vehicle.vehicle_class != VehicleClass.M1:
        return _vat_review(ReasonCode.VAT_CLASS_UNRESOLVED)
    if vehicle.vat_exception_mode is None:
        return _vat_review(ReasonCode.VAT_EXCEPTION_UNRESOLVED)
    if vehicle.vat_exception_mode == VatExceptionMode.FULL_DEDUCTION:
        return _VatAxis(
            treatment=VatTreatment.FULL_INPUT_VAT,
            ratio=ONE,
            requires_review=False,
            reasons=(ReasonCode.VAT_FULL_DEDUCTION,),
        )
    if vehicle.vat_exception_mode == VatExceptionMode.NONE:
        return _VatAxis(
            treatment=VatTreatment.LIMITED_INPUT_VAT,
            ratio=HALF,
            requires_review=False,
            reasons=(ReasonCode.VAT_M1_LIMIT,),
        )
    return _vat_review(ReasonCode.VAT_EXCEPTION_UNRESOLVED)


def _cit_hard_expected(rule: AxisRule) -> _CitAxis:
    # P2 ZPD is no-op when the rule does not require extra evidence.
    return _CitAxis(
        treatment=CitTreatment.EXCLUDED_FROM_VEHICLE_50,
        ratio=ZERO,
        requires_review=False,
        reasons=(rule.reason or ReasonCode.CIT_EXCLUDED_FROM_VEHICLE_50,),
    )


def _cit_candidate(context: TaxEvaluationContext) -> _CitAxis:
    vehicle = context.vehicle
    if vehicle.cit_exception_mode == CitExceptionMode.EXCLUDED:
        return _CitAxis(
            treatment=CitTreatment.EXCLUDED_FROM_VEHICLE_50,
            ratio=ZERO,
            requires_review=False,
            reasons=(ReasonCode.CIT_ACTIVITY_OR_SCOPE_EXCEPTION,),
        )
    if vehicle.cit_exception_mode is None:
        return _cit_review(ReasonCode.CIT_TREATMENT_UNRESOLVED)
    if vehicle.cit_exception_mode != CitExceptionMode.NONE:
        return _cit_review(ReasonCode.CIT_TREATMENT_UNRESOLVED)
    if vehicle.benefit_in_kind == BenefitInKind.APPLIED:
        return _CitAxis(
            treatment=CitTreatment.EXCLUDED_FROM_VEHICLE_50,
            ratio=ZERO,
            requires_review=False,
            reasons=(ReasonCode.CIT_BIK_APPLIES,),
        )
    if vehicle.benefit_in_kind is None:
        return _cit_review(ReasonCode.CIT_TREATMENT_UNRESOLVED)
    if vehicle.benefit_in_kind == BenefitInKind.NONE:
        return _CitAxis(
            treatment=CitTreatment.VEHICLE_50_ADD_BACK,
            ratio=HALF,
            requires_review=False,
            reasons=(ReasonCode.CIT_VEHICLE_50,),
        )
    return _cit_review(ReasonCode.CIT_TREATMENT_UNRESOLVED)


def _vat_review(reason: str) -> _VatAxis:
    return _VatAxis(treatment=None, ratio=None, requires_review=True, reasons=(reason,))


def _cit_review(reason: str) -> _CitAxis:
    return _CitAxis(treatment=None, ratio=None, requires_review=True, reasons=(reason,))
