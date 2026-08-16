from django.db import migrations


def rename_finestar_to_demo(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    Tenant.objects.filter(slug='finestar').update(slug='demo', name='Demo d.o.o.')


def rename_demo_to_finestar(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    Tenant.objects.filter(slug='demo', name='Demo d.o.o.').update(
        slug='finestar',
        name='Fine Star d.o.o.',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0005_tenant_invitation'),
    ]

    operations = [
        migrations.RunPython(rename_finestar_to_demo, rename_demo_to_finestar),
    ]
