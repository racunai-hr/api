from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('partners', '0005_partnerbankaccount_unique_iban'),
    ]

    operations = [
        migrations.AlterField(
            model_name='partnerbankaccount',
            name='bank_name',
            field=models.CharField(blank=True, max_length=100, verbose_name='Naziv banke'),
        ),
        migrations.AlterField(
            model_name='partnerbankaccount',
            name='bic',
            field=models.CharField(blank=True, max_length=50, verbose_name='BIC/SWIFT kod'),
        ),
    ]
