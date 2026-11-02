from datetime import date
from decimal import Decimal

from django.db import migrations, models


def backfill_parameters(apps, schema_editor):
    PayrollTaxYear = apps.get_model('payroll', 'PayrollTaxYear')
    Parameter = apps.get_model('payroll', 'PayrollLegalParameter')
    Rate = apps.get_model('payroll', 'MunicipalityIncomeTaxRate')
    for tax_year in PayrollTaxYear.objects.all():
        valid_from = date(tax_year.year, 1, 1)
        valid_to = date(tax_year.year, 12, 31)
        rows = {
            'MIO1_RATE': (tax_year.mio1_rate, 'PAYMENT_DATE'),
            'MIO2_RATE': (tax_year.mio2_rate, 'PAYMENT_DATE'),
            'MIO1_ONLY_RATE': (tax_year.mio1_only_rate, 'PAYMENT_DATE'),
            'ZO_RATE': (tax_year.zo_rate, 'PAYMENT_DATE'),
            'PERSONAL_ALLOWANCE_BASE': (tax_year.basic_personal_allowance, 'PAYMENT_DATE'),
            'TAX_BRACKET_ANNUAL': (tax_year.income_tax_annual_bracket_threshold, 'PAYMENT_DATE'),
            'PROTECTED_REFERENCE_NET': (tax_year.protected_account_reference_net, 'PAYMENT_DATE'),
            'MIN_WAGE_GROSS_FULLTIME': (tax_year.minimum_wage_gross, 'PAYROLL_MONTH'),
            'MIO1_REDUCTION_CEILING': (tax_year.mio1_base_reduction_threshold, 'PAYROLL_MONTH'),
            'MIO1_REDUCTION_FLOOR': (Decimal('700.00'), 'PAYROLL_MONTH'),
            'MIO1_REDUCTION_FLAT': (Decimal('300.00'), 'PAYROLL_MONTH'),
        }
        for code, (value, basis) in rows.items():
            Parameter.objects.update_or_create(
                code=code,
                valid_from=valid_from,
                defaults={'value': value, 'valid_to': valid_to, 'effective_basis': basis, 'source_reference': 'PayrollTaxYear'},
            )
    for rate in Rate.objects.all():
        rate.valid_from = date(rate.year, 1, 1)
        rate.valid_to = date(rate.year, 12, 31)
        rate.effective_basis = 'PAYMENT_DATE'
        rate.save(update_fields=['valid_from', 'valid_to', 'effective_basis'])


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0002_payslip_correctness'),
    ]

    operations = [
        migrations.CreateModel(
            name='PayrollLegalParameter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=64, verbose_name='Šifra parametra')),
                ('value', models.DecimalField(decimal_places=4, max_digits=14, verbose_name='Vrijednost')),
                ('valid_from', models.DateField(verbose_name='Vrijedi od')),
                ('valid_to', models.DateField(blank=True, null=True, verbose_name='Vrijedi do')),
                ('effective_basis', models.CharField(choices=[('PAYMENT_DATE', 'Datum isplate'), ('PAYROLL_MONTH', 'Mjesec obračuna'), ('ENTITLEMENT_PERIOD', 'Razdoblje prava'), ('EVENT_DATE', 'Datum događaja')], max_length=24, verbose_name='Osnovica datuma')),
                ('source_reference', models.CharField(blank=True, max_length=120, verbose_name='Izvor (NN)')),
                ('verified_by', models.CharField(blank=True, max_length=120, verbose_name='Potvrdio')),
                ('verified_at', models.DateTimeField(blank=True, null=True, verbose_name='Potvrđeno')),
            ],
            options={
                'verbose_name': 'Zakonski parametar plaće',
                'verbose_name_plural': 'Zakonski parametri plaće',
                'ordering': ['code', '-valid_from'],
            },
        ),
        migrations.AddConstraint(
            model_name='payrolllegalparameter',
            constraint=models.UniqueConstraint(fields=('code', 'valid_from'), name='unique_payroll_legal_parameter_from'),
        ),
        migrations.AddField(
            model_name='municipalityincometaxrate',
            name='effective_basis',
            field=models.CharField(default='PAYMENT_DATE', max_length=24, verbose_name='Osnovica datuma'),
        ),
        migrations.AddField(
            model_name='municipalityincometaxrate',
            name='source_reference',
            field=models.CharField(blank=True, max_length=120, verbose_name='Izvor (NN)'),
        ),
        migrations.AddField(
            model_name='municipalityincometaxrate',
            name='valid_from',
            field=models.DateField(blank=True, null=True, verbose_name='Vrijedi od'),
        ),
        migrations.AddField(
            model_name='municipalityincometaxrate',
            name='valid_to',
            field=models.DateField(blank=True, null=True, verbose_name='Vrijedi do'),
        ),
        migrations.AddField(
            model_name='payslip',
            name='parameters_snapshot',
            field=models.JSONField(blank=True, default=dict, verbose_name='Snimka parametara'),
        ),
        migrations.RunPython(backfill_parameters, migrations.RunPython.noop),
    ]
