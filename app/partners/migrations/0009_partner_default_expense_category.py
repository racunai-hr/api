import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0013_expense_account_override'),
        ('partners', '0008_partner_country_code_not_null_uniques'),
    ]

    operations = [
        migrations.AddField(
            model_name='partner',
            name='default_expense_category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='partners_with_default_category',
                to='expenses.expensecategory',
                verbose_name='Zadana vrsta troška',
            ),
        ),
    ]
