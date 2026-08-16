import importlib

from django.apps import apps
from django.test import TestCase, override_settings

from fiscal_gateway.models import DirectTenantConfig
from integrations.models import IntegrationConfig
from super_integration.models import SuperTenantConfig
from tenants.models import Tenant


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class SeedFinestarMigrationTests(TestCase):
    def test_seed_finestar_super_integration(self):
        migration_module = importlib.import_module(
            'integrations.migrations.0002_seed_finestar_super'
        )
        tenant, _ = Tenant.objects.get_or_create(
            slug='finestar',
            defaults={'name': 'Fine Star d.o.o.'},
        )
        IntegrationConfig.all_objects.filter(
            tenant=tenant,
            integration_type='eracun',
            environment='production',
        ).delete()
        SuperTenantConfig.all_objects.update_or_create(
            tenant=tenant,
            defaults={
                'username': 'user',
                'password': 'pass',
                'company_guid': 'guid-1',
                'is_active': True,
            },
        )

        migration_module.seed_finestar_super(apps, None)

        config = IntegrationConfig.all_objects.get(
            tenant=tenant,
            integration_type='eracun',
            provider='super',
            environment='production',
        )
        self.assertTrue(config.is_active)
        self.assertEqual(config.display_name, 'SUPER eRačun')

    def test_cutover_finestar_direct_production(self):
        migration_module = importlib.import_module(
            'integrations.migrations.0008_fiscal_prod_cutover'
        )
        tenant, _ = Tenant.objects.get_or_create(
            slug='finestar',
            defaults={'name': 'Fine Star d.o.o.'},
        )
        IntegrationConfig.all_objects.filter(
            tenant=tenant,
            integration_type='eracun',
            environment='production',
        ).delete()
        DirectTenantConfig.all_objects.update_or_create(
            tenant=tenant,
            defaults={'oib': '36619131370', 'is_active': True},
        )
        SuperTenantConfig.all_objects.update_or_create(
            tenant=tenant,
            defaults={
                'username': 'user',
                'password': 'pass',
                'company_guid': 'guid-1',
                'is_active': True,
            },
        )
        IntegrationConfig.all_objects.create(
            tenant=tenant,
            integration_type='eracun',
            provider='super',
            environment='production',
            is_active=True,
            display_name='SUPER eRačun',
        )

        migration_module.cutover_finestar_production(apps, None)

        direct = IntegrationConfig.all_objects.get(
            tenant=tenant,
            integration_type='eracun',
            provider='direct',
            environment='production',
        )
        self.assertTrue(direct.is_active)
        super_cfg = IntegrationConfig.all_objects.get(
            tenant=tenant,
            integration_type='eracun',
            provider='super',
            environment='production',
        )
        self.assertFalse(super_cfg.is_active)
