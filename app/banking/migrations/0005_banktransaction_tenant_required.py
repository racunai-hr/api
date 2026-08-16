# Make banktransaction.tenant required

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0004_banktransaction_expansion'),
        ('tenants', '0004_backfill_finestar_tenant'),
    ]

    operations = [
        migrations.AlterField(
            model_name='banktransaction',
            name='tenant',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='banking_banktransaction_set', to='tenants.tenant', verbose_name='Tenant'),
        ),
    ]
