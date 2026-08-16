from __future__ import annotations

from dataclasses import dataclass

from fiscal_gateway.constants import CIS_ENDPOINTS, CIS_ENV_DEMO, CIS_ENV_PROD, CIS_ENV_PTS
from .constants import IntegrationEnvironment, IntegrationProvider


@dataclass(frozen=True)
class ProviderProfile:
    api_base_url: str
    auth_scheme: str = 'basic'
    timeout_seconds: int = 30
    mps_service_url: str = ''


SUPER_PROFILES: dict[tuple[str, str], ProviderProfile] = {
    (IntegrationProvider.SUPER, IntegrationEnvironment.PRODUCTION): ProviderProfile(
        api_base_url='https://api.super.hr',
    ),
    (IntegrationProvider.SUPER, IntegrationEnvironment.TEST): ProviderProfile(
        api_base_url='https://apitest.super.hr',
    ),
}

CIS_PROFILES: dict[tuple[str, str], ProviderProfile] = {
    (IntegrationProvider.CIS, IntegrationEnvironment.PRODUCTION): ProviderProfile(
        api_base_url=CIS_ENDPOINTS[CIS_ENV_PROD],
    ),
    (IntegrationProvider.CIS, IntegrationEnvironment.TEST): ProviderProfile(
        api_base_url=CIS_ENDPOINTS[CIS_ENV_DEMO],
    ),
    (IntegrationProvider.CIS, IntegrationEnvironment.SANDBOX): ProviderProfile(
        api_base_url=CIS_ENDPOINTS[CIS_ENV_PTS],
    ),
}

def _direct_profile(environment: str) -> ProviderProfile:
    from django.conf import settings

    domibus = getattr(settings, 'DOMIBUS_WS_URL', '')
    mps = getattr(settings, 'MPS_SERVICE_URL', '')
    return ProviderProfile(api_base_url=domibus, mps_service_url=mps)


DIRECT_PROFILES: dict[tuple[str, str], ProviderProfile] = {
    (IntegrationProvider.DIRECT, IntegrationEnvironment.PRODUCTION): _direct_profile(
        IntegrationEnvironment.PRODUCTION
    ),
    (IntegrationProvider.DIRECT, IntegrationEnvironment.TEST): _direct_profile(
        IntegrationEnvironment.TEST
    ),
    (IntegrationProvider.DIRECT, IntegrationEnvironment.SANDBOX): _direct_profile(
        IntegrationEnvironment.SANDBOX
    ),
}

ALL_PROFILES = {**SUPER_PROFILES, **CIS_PROFILES, **DIRECT_PROFILES}


def get_provider_profile(provider: str, environment: str) -> ProviderProfile | None:
    return ALL_PROFILES.get((provider, environment))


def cis_env_for_integration(environment: str) -> str:
    mapping = {
        IntegrationEnvironment.PRODUCTION: CIS_ENV_PROD,
        IntegrationEnvironment.TEST: CIS_ENV_DEMO,
        IntegrationEnvironment.SANDBOX: CIS_ENV_PTS,
        IntegrationEnvironment.STAGING: CIS_ENV_DEMO,
    }
    return mapping.get(environment, CIS_ENV_DEMO)


def integration_env_for_cis(cis_env: str) -> str:
    mapping = {
        CIS_ENV_PROD: IntegrationEnvironment.PRODUCTION,
        CIS_ENV_DEMO: IntegrationEnvironment.TEST,
        CIS_ENV_PTS: IntegrationEnvironment.SANDBOX,
    }
    return mapping.get(cis_env, IntegrationEnvironment.TEST)
