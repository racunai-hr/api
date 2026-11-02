from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0025_accounting_classification'),
    ]

    operations = [
        migrations.AddField(
            model_name='expense',
            name='foreign_net_amount',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=15,
                null=True,
                verbose_name='Neto u valuti dokumenta',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='foreign_vat_amount',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Porez obračunan na izvornom dokumentu. Nije hrvatski odbitni pretporez.',
                max_digits=15,
                null=True,
                verbose_name='Strani PDV u valuti dokumenta',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='foreign_gross_amount',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=15,
                null=True,
                verbose_name='Bruto u valuti dokumenta',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='foreign_vat_country',
            field=models.CharField(blank=True, max_length=2, verbose_name='Zemlja stranog PDV-a'),
        ),
        migrations.AddField(
            model_name='expense',
            name='foreign_vat_rate',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=5,
                null=True,
                verbose_name='Stopa stranog PDV-a',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='foreign_vat_treatment',
            field=models.CharField(
                blank=True,
                choices=[
                    ('expense', 'Uključi u trošak nabave'),
                    ('refund_receivable', 'Potraživanje za EU VAT Refund'),
                ],
                max_length=32,
                verbose_name='Tretman stranog PDV-a',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='fx_rate',
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                help_text='Jedinica valute dokumenta za 1 EUR. Isti snapshot za sve iznose temeljnice.',
                max_digits=18,
                null=True,
                verbose_name='Tečaj',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='fx_rate_source',
            field=models.CharField(
                blank=True,
                choices=[('HNB', 'HNB'), ('ECB', 'ECB'), ('manual', 'Ručni unos')],
                max_length=16,
                verbose_name='Izvor tečaja',
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='fx_rate_date',
            field=models.DateField(blank=True, null=True, verbose_name='Datum tečaja'),
        ),
        migrations.AddField(
            model_name='expense',
            name='refund_eligibility',
            field=models.CharField(
                blank=True,
                help_text='candidate znači moguć kandidat za EU VAT Refund, ne sigurno povrativ iznos.',
                max_length=32,
                verbose_name='Kandidat za povrat',
            ),
        ),
    ]
