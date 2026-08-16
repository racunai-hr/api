from django.db import migrations


def seed_finestar_fiscal_config(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    FiscalTenantConfig = apps.get_model('fiscal_gateway', 'FiscalTenantConfig')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if tenant is None:
        return

    FiscalTenantConfig.objects.get_or_create(
        tenant=tenant,
        defaults={
            'oib': '36619131370',
            'cis_env': 'demo',
            'certificate_label': 'FISKAL 2',
            'is_active': True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('fiscal_gateway', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_finestar_fiscal_config, migrations.RunPython.noop),
    ]
