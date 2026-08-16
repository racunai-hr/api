"""VAT labels and rate helpers — not the PDV pipeline (Tax domain).

Re-exports pure mapping helpers until migrated from ``accounting``.
"""

from accounting.services.tax_forms.pdv.mapping import (
    invoice_rate_to_box,
    normalize_vat_rate,
)

__all__ = ['normalize_vat_rate', 'invoice_rate_to_box']
