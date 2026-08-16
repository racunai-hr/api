"""Obrazac ZP (Zbirna prijava) — EU izlazne isporuke.

Standard layout: docs/tax/FORM_IMPLEMENTATION_CONVENTION.md
Architecture: docs/tax/ZP_ARCHITECTURE.md
Fixtures: accounting/tests/fixtures/tax/zp/
"""

from accounting.services.tax_forms.zp.build import build_zp_payload

__all__ = ['build_zp_payload', 'create_zp_return_draft', 'mark_zp_submitted']


def __getattr__(name: str):
    if name == 'create_zp_return_draft':
        from accounting.services.tax_forms.zp.zp_returns import create_zp_return_draft

        return create_zp_return_draft
    if name == 'mark_zp_submitted':
        from accounting.services.tax_forms.zp.submit import mark_zp_submitted

        return mark_zp_submitted
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
