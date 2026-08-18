"""Tax domain — PDV, PDV-S, submission.

Maturity: L3
Legacy apps: accounting/services/tax_forms/, accounting/services/submission/
"""

from domains.tax.facade import (
    LAYER_MATURITY,
    MATURITY,
    REGISTRY_DOC,
    SubmissionService,
    TaxFormEngine,
    aggregate_vat_boxes,
    build_pdv_payload,
    classify,
    compute_vat_due,
    finalize_period,
)

__all__ = [
    'LAYER_MATURITY',
    'MATURITY',
    'REGISTRY_DOC',
    'SubmissionService',
    'TaxFormEngine',
    'aggregate_vat_boxes',
    'build_pdv_payload',
    'classify',
    'compute_vat_due',
    'finalize_period',
]
