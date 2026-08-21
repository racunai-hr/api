"""Build provider-agnostic external document view URLs (fail closed)."""

from __future__ import annotations

from django.conf import settings


def build_super_external_view_url(external_id: str | None) -> str | None:
    """Return SUPER human portal ViewDetails URL, or None if not configured.

    Uses SUPER_PORTAL_BASE_URL only — never invents a test host from api.super.hr.
    """
    guid = (external_id or '').strip()
    base = (getattr(settings, 'SUPER_PORTAL_BASE_URL', None) or '').strip().rstrip('/')
    if not guid or not base:
        return None
    return f'{base}/hr/Client/Invoice/ViewDetails/{guid}'
