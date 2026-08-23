"""STP v1 line-kind matrix. Candidate vs HARD_EXPECTED vs REVIEW per axis."""

from __future__ import annotations

from dataclasses import dataclass

from domains.tax.vehicle.contracts import (
    CitTreatment,
    LineRuleKind,
    ReasonCode,
    VatTreatment,
    VehicleLineKind,
)


@dataclass(frozen=True)
class AxisRule:
    rule_kind: LineRuleKind
    treatment: VatTreatment | CitTreatment | None
    reason: str | None = None


@dataclass(frozen=True)
class KindRule:
    vat: AxisRule
    cit: AxisRule


_NO_VAT = AxisRule(
    LineRuleKind.HARD_EXPECTED,
    VatTreatment.NO_INPUT_VAT,
    ReasonCode.VAT_NO_INPUT_TAX,
)
_CIT_EXCL_INS = AxisRule(
    LineRuleKind.HARD_EXPECTED,
    CitTreatment.EXCLUDED_FROM_VEHICLE_50,
    ReasonCode.CIT_EXCLUDED_FROM_VEHICLE_50,
)
_CIT_EXCL_PCMV = AxisRule(
    LineRuleKind.HARD_EXPECTED,
    CitTreatment.EXCLUDED_FROM_VEHICLE_50,
    ReasonCode.CIT_EXCLUDED_PCMV,
)
_CIT_EXCL_FEE = AxisRule(
    LineRuleKind.HARD_EXPECTED,
    CitTreatment.EXCLUDED_FROM_VEHICLE_50,
    ReasonCode.CIT_EXCLUDED_REGISTRATION_FEE,
)
_VAT_CAND = AxisRule(LineRuleKind.CANDIDATE, VatTreatment.LIMITED_INPUT_VAT)
_CIT_CAND = AxisRule(LineRuleKind.CANDIDATE, CitTreatment.VEHICLE_50_ADD_BACK)
_VAT_REV = AxisRule(LineRuleKind.REVIEW, None)
_CIT_REV_ENV = AxisRule(
    LineRuleKind.REVIEW,
    None,
    ReasonCode.CIT_ENVIRONMENTAL_FEE_UNRESOLVED,
)

KIND_RULES: dict[str, KindRule] = {
    VehicleLineKind.TECHNICAL_INSPECTION_SERVICE: KindRule(_VAT_CAND, _CIT_CAND),
    VehicleLineKind.REGISTRATION_ADMIN_SERVICE: KindRule(_VAT_CAND, _CIT_CAND),
    VehicleLineKind.ROAD_FEE_ANNUAL: KindRule(_NO_VAT, _CIT_EXCL_FEE),
    VehicleLineKind.ENVIRONMENTAL_FEE: KindRule(_NO_VAT, _CIT_REV_ENV),
    VehicleLineKind.ADMINISTRATIVE_FEE: KindRule(_NO_VAT, _CIT_EXCL_FEE),
    VehicleLineKind.MOTOR_VEHICLE_TAX: KindRule(_NO_VAT, _CIT_EXCL_PCMV),
    VehicleLineKind.INSURANCE_COMPULSORY: KindRule(_NO_VAT, _CIT_EXCL_INS),
    VehicleLineKind.UNCLASSIFIED: KindRule(_VAT_REV, AxisRule(LineRuleKind.REVIEW, None)),
}


def rule_for(kind: str | None) -> KindRule | None:
    if kind is None:
        return None
    return KIND_RULES.get(kind)
