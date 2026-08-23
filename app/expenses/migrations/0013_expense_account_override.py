from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0004_accounting_module_expansion'),
        ('expenses', '0012_expense_posting_profile'),
    ]

    operations = [
        migrations.AddField(
            model_name='expense',
            name='expense_account',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='expense_account_overrides',
                to='accounting.chartofaccounts',
                verbose_name='Rashodno konto (override)',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='expense_account_source',
            field=models.CharField(
                choices=[
                    ('manual_override', 'Ručna korekcija'),
                    ('category_default', 'Zadano konto vrste troška'),
                    ('partner_default', 'Zadana vrsta partnera'),
                    ('partner_history', 'Povijest partnera'),
                    ('posting_rule_fallback', 'Pravilo knjiženja'),
                ],
                default='category_default',
                max_length=32,
                verbose_name='Izvor rashodnog konta',
            ),
        ),
    ]
