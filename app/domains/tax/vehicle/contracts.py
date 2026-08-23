"""Pure VehicleTaxEvaluator contracts. No Django, no ADR-0019 boxes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class VehicleLineKind(StrEnum):
    TECHNICAL_INSPECTION_SERVICE = 'technical_inspection_service'
    REGISTRATION_ADMIN_SERVICE = 'registration_admin_service'
    ROAD_FEE_ANNUAL = 'road_fee_annual'
    ENVIRONMENTAL_FEE = 'environmental_fee'
    ADMINISTRATIVE_FEE = 'administrative_fee'
    MOTOR_VEHICLE_TAX = 'motor_vehicle_tax'
    INSURANCE_COMPULSORY = 'insurance_compulsory'
    UNCLASSIFIED = 'unclassified'


class LineRuleKind(StrEnum):
    HARD_EXPECTED = 'HARD_EXPECTED'
    CANDIDATE = 'CANDIDATE'
    REVIEW = 'REVIEW'


class VatTreatment(StrEnum):
    NO_INPUT_VAT = 'NO_INPUT_VAT'
    LIMITED_INPUT_VAT = 'LIMITED_INPUT_VAT'
    FULL_INPUT_VAT = 'FULL_INPUT_VAT'


class CitTreatment(StrEnum):
    EXCLUDED_FROM_VEHICLE_50 = 'EXCLUDED_FROM_VEHICLE_50'
    VEHICLE_50_ADD_BACK = 'VEHICLE_50_ADD_BACK'


class VehicleClass(StrEnum):
    M1 = 'm1'
    OTHER = 'other'


class VatExceptionMode(StrEnum):
    NONE = 'none'
    FULL_DEDUCTION = 'full_deduction'


class CitExceptionMode(StrEnum):
    NONE = 'none'
    EXCLUDED = 'excluded'


class BenefitInKind(StrEnum):
    NONE = 'none'
    APPLIED = 'applied'


class SupplierVatStatus(StrEnum):
    REGISTERED = 'registered'
    EXEMPT = 'exempt'
    UNKNOWN = 'unknown'


class EvidenceType(StrEnum):
    INVOICE = 'invoice'
    OTHER = 'other'
    UNKNOWN = 'unknown'


class ReasonCode(StrEnum):
    VAT_NO_INPUT_TAX = 'VAT_NO_INPUT_TAX'
    VAT_M1_LIMIT = 'VAT_M1_LIMIT'
    VAT_FULL_DEDUCTION = 'VAT_FULL_DEDUCTION'
    VAT_LINE_UNCLASSIFIED = 'VAT_LINE_UNCLASSIFIED'
    VAT_LINE_NOT_CLASSIFIED = 'VAT_LINE_NOT_CLASSIFIED'
    VAT_KIND_UNKNOWN = 'VAT_KIND_UNKNOWN'
    VAT_EVIDENCE_CONTRADICTION = 'VAT_EVIDENCE_CONTRADICTION'
    VAT_EVIDENCE_INSUFFICIENT = 'VAT_EVIDENCE_INSUFFICIENT'
    VAT_CLASS_UNRESOLVED = 'VAT_CLASS_UNRESOLVED'
    VAT_EXCEPTION_UNRESOLVED = 'VAT_EXCEPTION_UNRESOLVED'
    CIT_EXCLUDED_FROM_VEHICLE_50 = 'CIT_EXCLUDED_FROM_VEHICLE_50'
    CIT_EXCLUDED_PCMV = 'CIT_EXCLUDED_PCMV'
    CIT_EXCLUDED_REGISTRATION_FEE = 'CIT_EXCLUDED_REGISTRATION_FEE'
    CIT_VEHICLE_50 = 'CIT_VEHICLE_50'
    CIT_BIK_APPLIES = 'CIT_BIK_APPLIES'
    CIT_ACTIVITY_OR_SCOPE_EXCEPTION = 'CIT_ACTIVITY_OR_SCOPE_EXCEPTION'
    CIT_TREATMENT_UNRESOLVED = 'CIT_TREATMENT_UNRESOLVED'
    CIT_LINE_UNCLASSIFIED = 'CIT_LINE_UNCLASSIFIED'
    CIT_LINE_NOT_CLASSIFIED = 'CIT_LINE_NOT_CLASSIFIED'
    CIT_KIND_UNKNOWN = 'CIT_KIND_UNKNOWN'
    CIT_ENVIRONMENTAL_FEE_UNRESOLVED = 'CIT_ENVIRONMENTAL_FEE_UNRESOLVED'


@dataclass(frozen=True)
class LineFacts:
    vehicle_line_kind: VehicleLineKind | str | None
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal


@dataclass(frozen=True)
class VehicleFacts:
    vehicle_class: VehicleClass | None = None
    vat_exception_mode: VatExceptionMode | None = None
    cit_exception_mode: CitExceptionMode | None = None
    benefit_in_kind: BenefitInKind | None = None


@dataclass(frozen=True)
class DocumentFacts:
    evidence_type: EvidenceType | None = None
    supplier_vat_status: SupplierVatStatus | None = None


@dataclass(frozen=True)
class TaxEvaluationContext:
    line: LineFacts
    vehicle: VehicleFacts
    document: DocumentFacts


@dataclass(frozen=True)
class TaxEvaluationResult:
    vat_treatment: VatTreatment | None
    vat_deductible_ratio: Decimal | None
    vat_requires_review: bool
    cit_treatment: CitTreatment | None
    cit_addback_ratio: Decimal | None
    cit_requires_review: bool
    reason_codes: tuple[str, ...]
    requires_review: bool
