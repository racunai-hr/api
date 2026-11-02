from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0039_vatperiod_period_to'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='vatperiod',
            name='unique_vat_period_per_tenant',
        ),
        migrations.AddConstraint(
            model_name='vatperiod',
            constraint=models.UniqueConstraint(
                condition=models.Q(('period_to__isnull', True)),
                fields=('tenant', 'year', 'month'),
                name='unique_vat_period_month',
            ),
        ),
        migrations.AddConstraint(
            model_name='vatperiod',
            constraint=models.UniqueConstraint(
                condition=models.Q(('period_to__isnull', False)),
                fields=('tenant', 'year', 'month', 'period_to'),
                name='unique_vat_period_span',
            ),
        ),
    ]
