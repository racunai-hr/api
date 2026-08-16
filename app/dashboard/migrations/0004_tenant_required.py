import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0004_backfill_finestar_tenant'),
        ('dashboard', '0003_add_tenant_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='kpimetric',
            name='tenant',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='%(app_label)s_%(class)s_set',
                to='tenants.tenant',
                verbose_name='Tenant',
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='tenant',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='%(app_label)s_%(class)s_set',
                to='tenants.tenant',
                verbose_name='Tenant',
            ),
        ),
    ]
