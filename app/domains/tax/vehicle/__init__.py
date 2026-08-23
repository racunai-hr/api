"""Pure VehicleTaxEvaluator — not ADR-0019 classify()."""

from domains.tax.vehicle.contracts import (
    TaxEvaluationContext,
    TaxEvaluationResult,
)
from domains.tax.vehicle.evaluator import evaluate, input_vat_evidence_sufficient

__all__ = [
    'TaxEvaluationContext',
    'TaxEvaluationResult',
    'evaluate',
    'input_vat_evidence_sufficient',
]
