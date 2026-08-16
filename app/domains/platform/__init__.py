"""Platform domain — auth, RBAC, tenant infra, feature flags.

Maturity: L2
Legacy apps: accounts, tenants (auth/RBAC slice)
"""

from domains.platform.facade import (
    MATURITY,
    is_otp_service_host,
    resolve_service_host,
    turnstile_required_for_request,
    verify_turnstile,
)

__all__ = [
    'MATURITY',
    'is_otp_service_host',
    'resolve_service_host',
    'turnstile_required_for_request',
    'verify_turnstile',
]
