from django.db import IntegrityError
from django.test import TestCase, override_settings

from fiscal_gateway.models import DirectTenantConfig
from integrations.constants import (
    IntegrationEnvironment,
    IntegrationProvider,
    IntegrationType,
)
from integrations.errors import UnknownIntegrationError
from integrations.models import IntegrationConfig
from integrations.registry import create, discover_connectors, get_connector_class, registered_pairs
from integrations.repository import IntegrationRepository
from tenants.models import Tenant


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class RegistryTests(TestCase):
    def setUp(self):
        discover_connectors()

    def test_super_eracun_not_registered(self):
        self.assertNotIn(
            (IntegrationType.ERACUN, IntegrationProvider.SUPER),
            registered_pairs(),
        )

    def test_create_direct_connector(self):
        tenant = Tenant.objects.create(slug='reg-test', name='Registry Test')
        DirectTenantConfig.all_objects.create(
            tenant=tenant,
            oib='36619131370',
            is_active=True,
        )
        config = IntegrationConfig.all_objects.create(
            tenant=tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )
        connector = create(config)
        self.assertEqual(connector.__class__.__name__, 'DirectEracunConnector')
        self.assertEqual(connector.tenant, tenant)

    def test_super_config_cannot_create_connector(self):
        tenant = Tenant.objects.create(slug='super-blocked', name='Super Blocked')
        config = IntegrationConfig.all_objects.create(
            tenant=tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )
        with self.assertRaises(UnknownIntegrationError):
            create(config)

    def test_unknown_provider_raises(self):
        tenant = Tenant.objects.create(slug='unknown-test', name='Unknown Test')
        config = IntegrationConfig.all_objects.create(
            tenant=tenant,
            integration_type=IntegrationType.PAYMENT,
            provider=IntegrationProvider.MER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )
        with self.assertRaises(UnknownIntegrationError):
            create(config)

    def test_unknown_connector_class_raises(self):
        with self.assertRaises(UnknownIntegrationError):
            get_connector_class(IntegrationType.SHIPPING, IntegrationProvider.DIRECT)


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class RepositoryTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='repo-test', name='Repo Test')

    def test_get_active_returns_active_config(self):
        active = IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.TEST,
            is_active=True,
        )
        result = IntegrationRepository.get_active(
            self.tenant,
            IntegrationType.ERACUN,
            IntegrationEnvironment.PRODUCTION,
        )
        self.assertEqual(result.pk, active.pk)

    def test_unique_per_tenant_type_provider_env(self):
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )
        with self.assertRaises(IntegrityError):
            IntegrationConfig.all_objects.create(
                tenant=self.tenant,
                integration_type=IntegrationType.ERACUN,
                provider=IntegrationProvider.SUPER,
                environment=IntegrationEnvironment.PRODUCTION,
                is_active=False,
            )

    def test_unique_active_per_type_env(self):
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )
        with self.assertRaises(IntegrityError):
            IntegrationConfig.all_objects.create(
                tenant=self.tenant,
                integration_type=IntegrationType.ERACUN,
                provider=IntegrationProvider.DIRECT,
                environment=IntegrationEnvironment.PRODUCTION,
                is_active=True,
            )
