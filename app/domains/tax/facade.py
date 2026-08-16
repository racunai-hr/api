"""Tax domain facade — public API.

PDV pipeline remains frozen per ADR-0007; facades delegate to legacy services.
Registry SSOT: docs/tax/FORM_REGISTRY.md
"""

from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes, compute_vat_due
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from domains.tax.forms.protocol import TaxFormEngine

MATURITY = 'L3'

LAYER_MATURITY = {
    'ledger': 'L2',
    'forms': 'L3',
    'submission': 'L3',
    'validation': 'L2',
    'verification': 'L2',
    'joppd_builder': 'L0',
    'ai': 'L0',
}

REGISTRY_DOC = 'docs/tax/FORM_REGISTRY.md'

__all__ = [
    'LAYER_MATURITY',
    'MATURITY',
    'REGISTRY_DOC',
    'SubmissionService',
    'TaxFormEngine',
    'aggregate_vat_boxes',
    'build_pdv_payload',
    'compute_vat_due',
]
