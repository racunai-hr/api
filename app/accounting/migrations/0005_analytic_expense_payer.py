import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0007_private_settlement'),
        ('accounting', '0004_accounting_module_expansion'),
    ]

    operations = [
        migrations.AlterField(
            model_name='analyticaccount',
            name='counterparty_type',
            field=models.CharField(
                choices=[
                    ('partner', 'Partner (kupac)'),
                    ('supplier', 'Dobavljač'),
                    ('payer', 'Platitelj troška'),
                ],
                max_length=10,
                verbose_name='Tip subjekta',
            ),
        ),
        migrations.AddField(
            model_name='analyticaccount',
            name='expense_payer',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='analytic_accounts',
                to='expenses.expensepayer',
                verbose_name='Platitelj troška',
            ),
        ),
    ]
