"""TD-001: remove deprecated AnalyticAccount.supplier FK."""

from django.db import migrations


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('accounting', '0015_submissionevent_v1_fields'),
        ('expenses', '0008_td001_supplier_to_partner'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='analyticaccount',
            name='supplier',
        ),
    ]
