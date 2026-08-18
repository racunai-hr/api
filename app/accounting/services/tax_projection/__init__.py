"""Read-only VAT projection prepare (ADR-0019 Gate B). Does not write ledger or runs."""

from accounting.services.tax_projection.prepare import prepare_vat_projection

__all__ = ['prepare_vat_projection']
