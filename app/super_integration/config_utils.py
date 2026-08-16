# Private API — koristi samo unutar super_integration (integrations app koristi provider_loaders).
from __future__ import annotations

from super_integration.models import SuperTenantConfig
from tenants.models import Tenant


class SuperConfigError(Exception):
    """Raised when SUPER tenant config is missing or incomplete."""


def get_active_super_config(tenant: Tenant, *, require_active: bool = True) -> SuperTenantConfig:
    config = SuperTenantConfig.all_objects.filter(tenant=tenant).first()
    if config is None:
        raise SuperConfigError(
            f'Nema SUPER konfiguracije za tenant "{tenant.slug}". '
            f'Kreirajte je u Admin → SUPER konfiguracije.'
        )
    if require_active and not config.is_active:
        raise SuperConfigError(
            f'SUPER konfiguracija za "{tenant.slug}" postoji, ali nije aktivna. '
            f'Uključite "Aktivna integracija" u adminu.'
        )
    missing = []
    if not (config.username or '').strip():
        missing.append('username')
    if not (config.password or '').strip():
        missing.append('password')
    if not (config.company_guid or '').strip():
        missing.append('company_guid')
    if not (config.api_base_url or '').strip():
        missing.append('api_base_url')
    if missing:
        raise SuperConfigError(
            f'SUPER konfiguracija za "{tenant.slug}" je nepotpuna (nedostaje: {", ".join(missing)}). '
            f'Dopunite polja u adminu.'
        )
    return config
