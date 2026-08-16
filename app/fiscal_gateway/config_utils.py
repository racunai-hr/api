from __future__ import annotations

from pathlib import Path

from django.conf import settings

from fiscal_gateway.constants import CIS_ENV_DEMO
from fiscal_gateway.models import FiscalTenantConfig
from tenants.models import Tenant


class FiscalConfigError(Exception):
    """Raised when fiscal tenant config is missing or incomplete."""


def resolve_p12_path(config: FiscalTenantConfig | None) -> str:
    if config and (config.p12_path or '').strip():
        path = config.p12_path.strip()
    else:
        path = (settings.FISCAL_CERT_P12_PATH or '').strip()

    if path and Path(path).is_file():
        return path

    fallback = Path('/run/secrets/fiscal-cert') / '36619131370.F2.2.p12'
    if fallback.is_file():
        return str(fallback)

    return path


def resolve_p12_password(config: FiscalTenantConfig | None) -> str:
    if config and (config.p12_password or '').strip():
        return config.p12_password.strip()
    return (settings.FISCAL_CERT_P12_PASSWORD or '').strip()


def resolve_cis_env(config: FiscalTenantConfig | None) -> str:
    if config:
        return config.cis_env
    return settings.FISCAL_CIS_ENV or CIS_ENV_DEMO


def get_active_fiscal_config(tenant: Tenant, *, require_active: bool = True) -> FiscalTenantConfig:
    config = FiscalTenantConfig.all_objects.filter(tenant=tenant).first()
    if config is None:
        raise FiscalConfigError(
            f'Nema fiskalne konfiguracije za tenant "{tenant.slug}". '
            f'Kreirajte je u Admin → Fiskalne konfiguracije.'
        )
    if require_active and not config.is_active:
        raise FiscalConfigError(
            f'Fiskalna konfiguracija za "{tenant.slug}" postoji, ali nije aktivna.'
        )
    if not (config.oib or '').strip():
        raise FiscalConfigError(f'Fiskalna konfiguracija za "{tenant.slug}" nema OIB.')
    p12_path = resolve_p12_path(config)
    p12_password = resolve_p12_password(config)
    if not p12_path:
        raise FiscalConfigError(
            f'Nedostaje putanja .p12 (polje u adminu ili FISCAL_CERT_P12_PATH).'
        )
    if not p12_password:
        raise FiscalConfigError(
            f'Nedostaje lozinka .p12 (polje u adminu ili FISCAL_CERT_P12_PASSWORD).'
        )
    return config
