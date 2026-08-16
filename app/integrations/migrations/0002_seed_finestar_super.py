from django.db import migrations


def seed_finestar_super(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')
    SuperTenantConfig = apps.get_model('super_integration', 'SuperTenantConfig')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if tenant is None:
        return

    if not SuperTenantConfig.objects.filter(tenant=tenant, is_active=True).exists():
        return

    IntegrationConfig.objects.update_or_create(
        tenant=tenant,
        integration_type='eracun',
        provider='super',
        environment='production',
        defaults={
            'is_active': True,
            'display_name': 'SUPER eRačun',
        },
    )


def unseed_finestar_super(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if tenant is None:
        return

    IntegrationConfig.objects.filter(
        tenant=tenant,
        integration_type='eracun',
        provider='super',
        environment='production',
        display_name='SUPER eRačun',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0001_initial'),
        ('super_integration', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_finestar_super, unseed_finestar_super),
    ]
