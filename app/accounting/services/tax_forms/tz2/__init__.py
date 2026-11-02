"""Obrazac TZ 2 — turistička članarina.

Standard layout: docs/tax/FORM_IMPLEMENTATION_CONVENTION.md
Architecture: docs/tax/TZ2_ARCHITECTURE.md
ADR: docs/architecture/ADR-0031-tz2-return.md
"""

from accounting.services.tax_forms.tz2.build import build_tz2_payload

__all__ = ['build_tz2_payload', 'create_tz2_return_draft', 'mark_tz2_submitted']


def __getattr__(name: str):
    if name == 'create_tz2_return_draft':
        from accounting.services.tax_forms.tz2.tz2_returns import create_tz2_return_draft

        return create_tz2_return_draft
    if name == 'mark_tz2_submitted':
        from accounting.services.tax_forms.tz2.submit import mark_tz2_submitted

        return mark_tz2_submitted
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
