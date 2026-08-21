"""Pure tax classification engine (ADR-0019). Does not write VATLedgerEntry."""

from domains.tax.classification.contracts import (
    ClassifiedWithoutRowsError,
    Direction,
    EventKind,
    OriginTaxEffect,
    OriginTaxOwner,
    Outcome,
    PartnerProvenance,
    PartnerSnapshot,
    ProposedLedgerRow,
    TaxClassificationResult,
    TaxDocumentInput,
    TaxRelevance,
)
from domains.tax.classification.engine import classify, reconcile_rc_bases
from domains.tax.classification.finalize import finalize_period

__all__ = [
    'ClassifiedWithoutRowsError',
    'Direction',
    'EventKind',
    'OriginTaxEffect',
    'OriginTaxOwner',
    'Outcome',
    'PartnerProvenance',
    'PartnerSnapshot',
    'ProposedLedgerRow',
    'TaxClassificationResult',
    'TaxDocumentInput',
    'TaxRelevance',
    'classify',
    'finalize_period',
    'reconcile_rc_bases',
]
