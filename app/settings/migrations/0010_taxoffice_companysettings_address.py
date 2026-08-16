import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0009_tenant_required'),
    ]

    operations = [
        migrations.CreateModel(
            name='TaxOffice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=4, unique=True, verbose_name='Šifra')),
                ('name', models.CharField(max_length=200, verbose_name='Naziv')),
                ('city', models.CharField(max_length=100, verbose_name='Grad')),
            ],
            options={
                'verbose_name': 'Porezni ured',
                'verbose_name_plural': 'Porezni uredi',
                'ordering': ['code'],
            },
        ),
        migrations.AlterField(
            model_name='companysettings',
            name='company_address',
            field=models.TextField(verbose_name='Adresa tvrtke (deprecated)'),
        ),
        migrations.AddField(
            model_name='companysettings',
            name='street',
            field=models.CharField(blank=True, max_length=200, verbose_name='Ulica'),
        ),
        migrations.AddField(
            model_name='companysettings',
            name='house_number',
            field=models.CharField(blank=True, max_length=20, verbose_name='Kućni broj'),
        ),
        migrations.AddField(
            model_name='companysettings',
            name='postal_code',
            field=models.CharField(blank=True, max_length=10, verbose_name='Poštanski broj'),
        ),
        migrations.AddField(
            model_name='companysettings',
            name='city',
            field=models.CharField(blank=True, max_length=100, verbose_name='Grad'),
        ),
        migrations.AddField(
            model_name='companysettings',
            name='country',
            field=models.CharField(default='HR', max_length=2, verbose_name='Država'),
        ),
        migrations.AddField(
            model_name='companysettings',
            name='tax_office',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='companies',
                to='settings.taxoffice',
                verbose_name='Porezni ured',
            ),
        ),
    ]
