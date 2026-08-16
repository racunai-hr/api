from django.db import migrations


def migrate_super_test_mode(apps, schema_editor):
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')
    SuperTenantConfig = apps.get_model('super_integration', 'SuperTenantConfig')

    for super_config in SuperTenantConfig.objects.filter(is_test_mode=True):
        IntegrationConfig.objects.filter(
            tenant=super_config.tenant,
            integration_type='eracun',
            provider='super',
            environment='production',
        ).update(environment='test')


def reverse_migrate(apps, schema_editor):
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')
    SuperTenantConfig = apps.get_model('super_integration', 'SuperTenantConfig')

    for super_config in SuperTenantConfig.objects.filter(is_test_mode=True):
        IntegrationConfig.objects.filter(
            tenant=super_config.tenant,
            integration_type='eracun',
            provider='super',
            environment='test',
        ).update(environment='production')


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0003_seed_finestar_cis'),
        ('super_integration', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(migrate_super_test_mode, reverse_migrate),
    ]
