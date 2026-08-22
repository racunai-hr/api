"""M1.7 regression — no direct SUPER HTTP from racunai-api."""

from __future__ import annotations

import importlib
import pkgutil
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase, override_settings

from fiscal_gateway.models import DirectTenantConfig
from integrations.constants import (
    IntegrationEnvironment,
    IntegrationProvider,
    IntegrationType,
)
from integrations.manager import IntegrationManager
from integrations.models import IntegrationConfig
from integrations.registry import discover_connectors, registered_pairs
from integrations.repository import IntegrationRepository
from super_integration.models import SuperTenantConfig
from tenants.models import Tenant


def _iter_app_modules():
    for app_config in apps.get_app_configs():
        if not app_config.name.startswith(('integrations', 'super_integration', 'expenses', 'fiscal_gateway')):
            continue
        package = importlib.import_module(app_config.name)
        if not hasattr(package, '__path__'):
            continue
        for module_info in pkgutil.walk_packages(package.__path__, prefix=f'{app_config.name}.'):
            if '.tests' in module_info.name or module_info.name.endswith('.migrations'):
                continue
            yield module_info.name


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class M17SuperRemovalTests(TestCase):
    def setUp(self):
        discover_connectors()
        self.tenant = Tenant.objects.create(slug='m17-test', name='M17 Test')

    def test_super_eracun_connector_not_registered(self):
        self.assertNotIn(
            (IntegrationType.ERACUN, IntegrationProvider.SUPER),
            registered_pairs(),
        )

    def test_no_superclient_import_in_runtime_modules(self):
        for module_name in _iter_app_modules():
            module = importlib.import_module(module_name)
            source_path = getattr(module, '__file__', '') or ''
            if not source_path.endswith('.py'):
                continue
            with open(source_path, encoding='utf-8') as handle:
                source = handle.read()
            self.assertNotIn(
                'from super_integration.client import',
                source,
                msg=f'{module_name} still imports SuperClient',
            )
            self.assertNotIn(
                'SuperClient(',
                source,
                msg=f'{module_name} still references SuperClient',
            )

    def test_resolve_never_returns_super_with_legacy_super_active(self):
        SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='user',
            password='pass',
            company_guid='legacy-guid',
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

    def test_a2_scenario_direct_integration_without_active_direct_tenant(self):
        DirectTenantConfig.all_objects.create(
            tenant=self.tenant,
            oib='36619131370',
            is_active=False,
        )
        SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='user',
            password='pass',
            company_guid='a2-guid',
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
        connector = IntegrationManager.get_connector(
            self.tenant,
            IntegrationType.ERACUN,
            environment=IntegrationEnvironment.PRODUCTION,
        )
        self.assertIsNone(connector)

    def test_direct_path_unchanged_when_valid(self):
        DirectTenantConfig.all_objects.create(
            tenant=self.tenant,
            oib='36619131370',
            is_active=True,
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
        connector = IntegrationManager.get_connector(
            self.tenant,
            IntegrationType.ERACUN,
            environment=IntegrationEnvironment.PRODUCTION,
        )
        self.assertEqual(connector.__class__.__name__, 'DirectEracunConnector')

    @patch('super_integration.admin.GatewayV1Client')
    def test_admin_connectivity_uses_gateway_not_superclient(self, mock_client_cls):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from settings.models import CompanySettings
        from super_integration.admin import SuperTenantConfigAdmin
        from super_integration.models import SuperTenantConfig

        CompanySettings.all_objects.create(
            tenant=self.tenant,
            company_name='M17 Co',
            company_address='Test adresa',
            vat_number='36619131370',
            company_phone='0912345678',
            company_email='m17@test.hr',
        )
        config = SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='legacy',
            password='legacy',
            company_guid='guid',
            is_active=True,
        )
        mock_client_cls.return_value.provider_capabilities.return_value = {
            'outbound_readiness': {'ready': True},
            'inbound_readiness': {'active_binding': True},
        }
        request = RequestFactory().post('/')
        request.session = 'session'
        request._messages = FallbackStorage(request)
        admin_instance = SuperTenantConfigAdmin(SuperTenantConfig, None)
        admin_instance.test_gateway_connection(
            request,
            SuperTenantConfig.all_objects.filter(pk=config.pk),
        )
        mock_client_cls.assert_called_once()
        mock_client_cls.return_value.provider_capabilities.assert_called_once()
