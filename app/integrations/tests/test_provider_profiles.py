from django.test import TestCase

from integrations.constants import IntegrationEnvironment, IntegrationProvider
from integrations.provider_profiles import (
    cis_env_for_integration,
    get_provider_profile,
    integration_env_for_cis,
)
from fiscal_gateway.constants import CIS_ENV_DEMO, CIS_ENV_PROD


class ProviderProfileTests(TestCase):
    def test_super_production_profile(self):
        profile = get_provider_profile(IntegrationProvider.SUPER, IntegrationEnvironment.PRODUCTION)
        self.assertEqual(profile.api_base_url, 'https://api.super.hr')

    def test_super_test_profile(self):
        profile = get_provider_profile(IntegrationProvider.SUPER, IntegrationEnvironment.TEST)
        self.assertEqual(profile.api_base_url, 'https://apitest.super.hr')

    def test_cis_env_mapping(self):
        self.assertEqual(cis_env_for_integration(IntegrationEnvironment.PRODUCTION), CIS_ENV_PROD)
        self.assertEqual(cis_env_for_integration(IntegrationEnvironment.TEST), CIS_ENV_DEMO)
        self.assertEqual(integration_env_for_cis(CIS_ENV_PROD), IntegrationEnvironment.PRODUCTION)
