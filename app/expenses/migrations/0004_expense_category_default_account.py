# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0003_tenant_required'),
        ('accounting', '0004_accounting_module_expansion'),
    ]

    operations = [
        migrations.AddField(
            model_name='expensecategory',
            name='default_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expense_categories', to='accounting.chartofaccounts', verbose_name='Konto rashoda'),
        ),
        migrations.AlterField(
            model_name='expense',
            name='currency',
            field=models.CharField(default='EUR', max_length=3, verbose_name='Valuta'),
        ),
    ]
