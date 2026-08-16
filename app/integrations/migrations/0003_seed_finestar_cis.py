from django.db import migrations


def seed_finestar_cis(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')
    FiscalTenantConfig = apps.get_model('fiscal_gateway', 'FiscalTenantConfig')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if tenant is None:
        return

    if not FiscalTenantConfig.objects.filter(tenant=tenant, is_active=True).exists():
        return

    IntegrationConfig.objects.update_or_create(
        tenant=tenant,
        integration_type='fiscalization',
        provider='cis',
        environment='production',
        defaults={
            'is_active': True,
            'display_name': 'CIS fiskalizacija',
        },
    )


def unseed_finestar_cis(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if tenant is None:
        return

    IntegrationConfig.objects.filter(
        tenant=tenant,
        integration_type='fiscalization',
        provider='cis',
        environment='production',
        display_name='CIS fiskalizacija',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0002_seed_finestar_super'),
        ('fiscal_gateway', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_finestar_cis, unseed_finestar_cis),
    ]
