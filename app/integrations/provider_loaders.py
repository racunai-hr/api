from __future__ import annotations

from .constants import IntegrationProvider, IntegrationType
from .errors import InactiveIntegrationError, UnknownIntegrationError
from .models import IntegrationConfig
from .provider_profiles import cis_env_for_integration, get_provider_profile


def load_provider_config(integration_config: IntegrationConfig):
    """Return typed provider config for the given IntegrationConfig."""
    if not integration_config.is_active:
        raise InactiveIntegrationError(
            f'Integracija {integration_config} nije aktivna.'
        )

    key = (integration_config.integration_type, integration_config.provider)
    loader = _LOADERS.get(key)
    if loader is None:
        raise UnknownIntegrationError(
            f'Nema loadera za {integration_config.integration_type}/'
            f'{integration_config.provider}'
        )
    config = loader(integration_config)
    _apply_provider_profile(integration_config, config)
    return config


def _apply_provider_profile(integration_config: IntegrationConfig, config) -> None:
    profile = get_provider_profile(
        integration_config.provider,
        integration_config.environment,
    )
    if profile is None:
        return

    if integration_config.provider == IntegrationProvider.CIS and hasattr(config, 'cis_env'):
        config.cis_env = cis_env_for_integration(integration_config.environment)

    if integration_config.provider == IntegrationProvider.DIRECT:
        if profile and hasattr(config, 'domibus_ws_url') and not config.domibus_ws_url:
            config.domibus_ws_url = profile.api_base_url
        if profile and hasattr(config, 'mps_service_url') and not config.mps_service_url:
            config.mps_service_url = profile.mps_service_url


def _load_fiskal_platform_fiscalization(integration_config: IntegrationConfig):
    from fiscal_gateway.client.platform_client import FiscalPlatformConfig

    return FiscalPlatformConfig.from_tenant(integration_config.tenant)


def _load_cis_fiscalization(integration_config: IntegrationConfig):
    from fiscal_gateway.models import FiscalTenantConfig

    try:
        config = FiscalTenantConfig.all_objects.get(tenant=integration_config.tenant)
    except FiscalTenantConfig.DoesNotExist as exc:
        raise InactiveIntegrationError(
            f'Nema fiskalne konfiguracije za tenant {integration_config.tenant.slug}.'
        ) from exc

    if not config.is_active:
        raise InactiveIntegrationError(
            f'Fiskalna konfiguracija za {integration_config.tenant.slug} nije aktivna.'
        )
    return config


def _load_direct_eracun(integration_config: IntegrationConfig):
    from fiscal_gateway.models import DirectTenantConfig

    try:
        config = DirectTenantConfig.all_objects.get(tenant=integration_config.tenant)
    except DirectTenantConfig.DoesNotExist as exc:
        raise InactiveIntegrationError(
            f'Nema DIRECT konfiguracije za tenant {integration_config.tenant.slug}.'
        ) from exc

    if not config.is_active:
        raise InactiveIntegrationError(
            f'DIRECT konfiguracija za {integration_config.tenant.slug} nije aktivna.'
        )
    return config


def _load_otp_payment(integration_config: IntegrationConfig):
    from banking.config import get_active_otp_provider
    from banking.provider_models import BankConnection

    provider = get_active_otp_provider()
    if provider is None:
        raise InactiveIntegrationError('OTP provider nije konfiguriran.')

    connection = (
        BankConnection.all_objects.filter(
            tenant=integration_config.tenant,
            bank_provider=provider,
            status='active',
        )
        .select_related('bank_provider', 'bank_account')
        .first()
    )
    if connection is None:
        raise InactiveIntegrationError(
            f'Nema aktivne OTP veze za tenant {integration_config.tenant.slug}.'
        )

    class OtpProviderConfig:
        def __init__(self, conn):
            self.tenant = conn.tenant
            self.is_active = True
            self.connection = conn
            self.provider = conn.bank_provider

    return OtpProviderConfig(connection)


_LOADERS = {
    (IntegrationType.ERACUN, IntegrationProvider.DIRECT): _load_direct_eracun,
    (IntegrationType.FISCALIZATION, IntegrationProvider.CIS): _load_cis_fiscalization,
    (IntegrationType.FISCALIZATION, IntegrationProvider.FISKAL_PLATFORM): _load_fiskal_platform_fiscalization,
    (IntegrationType.PAYMENT, IntegrationProvider.OTP): _load_otp_payment,
}
