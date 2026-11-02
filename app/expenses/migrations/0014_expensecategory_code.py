from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0013_expense_account_override'),
    ]

    operations = [
        migrations.AddField(
            model_name='expensecategory',
            name='code',
            field=models.CharField(
                blank=True,
                help_text='Stabilni strojni identitet vrste troška. Bez koda je NULL, ne prazan string.',
                max_length=64,
                null=True,
                verbose_name='Šifra kategorije',
            ),
        ),
        migrations.AddConstraint(
            model_name='expensecategory',
            constraint=models.UniqueConstraint(
                condition=models.Q(('code__isnull', False)),
                fields=('tenant', 'code'),
                name='unique_expense_category_code_per_tenant',
            ),
        ),
        migrations.AddConstraint(
            model_name='expensecategory',
            constraint=models.CheckConstraint(
                check=models.Q(('code', ''), _negated=True),
                name='expense_category_code_not_empty_string',
            ),
        ),
    ]
