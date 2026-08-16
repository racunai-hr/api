# M1.6/M1.8 — Fine Star production cutover: SUPER → DIRECT eRačun

from django.db import migrations


def cutover_finestar_production(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')
    DirectTenantConfig = apps.get_model('fiscal_gateway', 'DirectTenantConfig')
    FiscalTenantConfig = apps.get_model('fiscal_gateway', 'FiscalTenantConfig')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if tenant is None:
        return

    DirectTenantConfig.objects.update_or_create(
        tenant=tenant,
        defaults={
            'oib': '36619131370',
            'is_active': True,
        },
    )

    IntegrationConfig.objects.filter(
        tenant=tenant,
        integration_type='eracun',
        provider='super',
        environment='production',
    ).update(is_active=False)

    IntegrationConfig.objects.update_or_create(
        tenant=tenant,
        integration_type='eracun',
        provider='direct',
        environment='production',
        defaults={
            'is_active': True,
            'display_name': 'DIRECT AS4 (produkcija)',
        },
    )

    fiscal = FiscalTenantConfig.objects.filter(tenant=tenant, is_active=True).first()
    if fiscal is not None and fiscal.cis_env == 'demo':
        fiscal.cis_env = 'prod'
        fiscal.save(update_fields=['cis_env'])


def rollback_finestar_production(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')
    FiscalTenantConfig = apps.get_model('fiscal_gateway', 'FiscalTenantConfig')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if tenant is None:
        return

    IntegrationConfig.objects.filter(
        tenant=tenant,
        integration_type='eracun',
        provider='direct',
        environment='production',
        display_name='DIRECT AS4 (produkcija)',
    ).update(is_active=False)

    IntegrationConfig.objects.filter(
        tenant=tenant,
        integration_type='eracun',
        provider='super',
        environment='production',
    ).update(is_active=True)

    fiscal = FiscalTenantConfig.objects.filter(tenant=tenant, is_active=True).first()
    if fiscal is not None and fiscal.cis_env == 'prod':
        fiscal.cis_env = 'demo'
        fiscal.save(update_fields=['cis_env'])


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0007_otp_banking_foundation'),
        ('fiscal_gateway', '0003_faza3_direct_as4'),
    ]

    operations = [
        migrations.RunPython(cutover_finestar_production, rollback_finestar_production),
    ]
