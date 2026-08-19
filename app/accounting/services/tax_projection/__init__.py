"""VAT tax projection package (ADR-0019 Gates B–E0)."""

from accounting.services.tax_projection.adopt import adopt_legacy_projection
from accounting.services.tax_projection.apply import apply_vat_projection
from accounting.services.tax_projection.prepare import prepare_vat_projection
from accounting.services.tax_projection.rebuild import RebuildResult, rebuild_vat_ledger

__all__ = [
    'adopt_legacy_projection',
    'apply_vat_projection',
    'prepare_vat_projection',
    'rebuild_vat_ledger',
    'RebuildResult',
]
