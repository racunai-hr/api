"""Pure tax classification engine (ADR-0019). Does not write VATLedgerEntry."""

from domains.tax.classification.contracts import (
    ClassifiedWithoutRowsError,
    Direction,
    EventKind,
    OriginTaxEffect,
    Outcome,
    PartnerProvenance,
    PartnerSnapshot,
    ProposedLedgerRow,
    TaxClassificationResult,
    TaxDocumentInput,
)
from domains.tax.classification.engine import classify, reconcile_rc_bases
from domains.tax.classification.finalize import finalize_period

__all__ = [
    'ClassifiedWithoutRowsError',
    'Direction',
    'EventKind',
    'OriginTaxEffect',
    'Outcome',
    'PartnerProvenance',
    'PartnerSnapshot',
    'ProposedLedgerRow',
    'TaxClassificationResult',
    'TaxDocumentInput',
    'classify',
    'finalize_period',
    'reconcile_rc_bases',
]
