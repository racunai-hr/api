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


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr', USE_SUPER_ERACUN_FALLBACK=True)
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

    @override_settings(USE_SUPER_ERACUN_FALLBACK=True)
    def test_super_fallback_when_no_direct_config(self):
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

        with self.assertWarns(DeprecationWarning):
            config = IntegrationRepository.resolve_eracun_config(
                self.tenant,
                IntegrationEnvironment.PRODUCTION,
            )
        self.assertEqual(config.provider, IntegrationProvider.SUPER)

    @override_settings(USE_SUPER_ERACUN_FALLBACK=False)
    def test_no_super_fallback_when_flag_disabled(self):
        SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='user',
            password='pass',
            company_guid='guid-3',
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
