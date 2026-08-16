from django.db import migrations


def create_finestar_tenant(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    Tenant.objects.get_or_create(
        slug='finestar',
        defaults={'name': 'Fine Star d.o.o.', 'is_active': True},
    )


def remove_finestar_tenant(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    Tenant.objects.filter(slug='finestar').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_finestar_tenant, remove_finestar_tenant),
    ]
