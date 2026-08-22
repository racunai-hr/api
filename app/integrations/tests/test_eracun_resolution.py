from django.test import TestCase, override_settings

from fiscal_gateway.models import DirectTenantConfig
from integrations.constants import (
    IntegrationEnvironment,
    IntegrationProvider,
    IntegrationType,
)
from integrations.models import IntegrationConfig
from integrations.repository import IntegrationRepository
from super_integration.models import SuperTenantConfig
from tenants.models import Tenant


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class EracunConfigResolutionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='resolve-test', name='Resolve Test')

    def test_prefers_direct_when_both_configured(self):
        DirectTenantConfig.all_objects.create(
            tenant=self.tenant,
            oib='36619131370',
            is_active=True,
        )
        SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='user',
            password='pass',
            company_guid='guid-1',
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=False,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )

        config = IntegrationRepository.resolve_eracun_config(
            self.tenant,
            IntegrationEnvironment.PRODUCTION,
        )
        self.assertEqual(config.provider, IntegrationProvider.DIRECT)

    def test_a2_direct_config_active_but_direct_tenant_inactive_returns_none(self):
        """A2: SUPER must not win when DirectTenantConfig is inactive."""
        DirectTenantConfig.all_objects.create(
            tenant=self.tenant,
            oib='36619131370',
            is_active=False,
        )
        SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='user',
            password='pass',
            company_guid='guid-a2',
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=False,
        )

        config = IntegrationRepository.resolve_eracun_config(
            self.tenant,
            IntegrationEnvironment.PRODUCTION,
        )
        self.assertIsNone(config)

    def test_only_super_config_active_returns_none(self):
        SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='user',
            password='pass',
            company_guid='guid-2',
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )

        config = IntegrationRepository.resolve_eracun_config(
            self.tenant,
            IntegrationEnvironment.PRODUCTION,
        )
        self.assertIsNone(config)

    def test_returns_none_when_no_integration(self):
        config = IntegrationRepository.resolve_eracun_config(
            self.tenant,
            IntegrationEnvironment.PRODUCTION,
        )
        self.assertIsNone(config)
