"""Platform domain facade — public API.

Re-exports from legacy ``tenants`` app until services migrate here.
"""

from tenants.service_hosts import is_otp_service_host, resolve_service_host
from tenants.turnstile import turnstile_required_for_request, verify_turnstile

MATURITY = 'L2'

__all__ = [
    'MATURITY',
    'is_otp_service_host',
    'resolve_service_host',
    'turnstile_required_for_request',
    'verify_turnstile',
]
