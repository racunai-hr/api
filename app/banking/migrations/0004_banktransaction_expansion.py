# Generated manually for banking module expansion

import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


def delete_empty_transactions(apps, schema_editor):
    BankTransaction = apps.get_model('banking', 'BankTransaction')
    BankTransaction.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0003_tenant_required'),
        ('payments', '0003_tenant_required'),
    ]

    operations = [
        migrations.RunPython(delete_empty_transactions, migrations.RunPython.noop),
        migrations.AddField(
            model_name='banktransaction',
            name='amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.01'), max_digits=15, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))], verbose_name='Iznos'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='counterparty_iban',
            field=models.CharField(blank=True, max_length=34, verbose_name='IBAN druge strane'),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='counterparty_name',
            field=models.CharField(blank=True, max_length=200, verbose_name='Naziv druge strane'),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='currency',
            field=models.CharField(default='EUR', max_length=3, verbose_name='Valuta'),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='description',
            field=models.TextField(blank=True, verbose_name='Opis'),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='match_status',
            field=models.CharField(choices=[('unmatched', 'Neusklađeno'), ('suggested', 'Prijedlog'), ('matched', 'Usklađeno')], default='unmatched', max_length=10, verbose_name='Status usklađivanja'),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='matched_payment',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bank_transactions', to='payments.payment', verbose_name='Povezano plaćanje'),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='reference',
            field=models.CharField(blank=True, max_length=100, verbose_name='Poziv na broj'),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='tenant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='banking_banktransaction_set', to='tenants.tenant', verbose_name='Tenant'),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='transaction_date',
            field=models.DateField(default='2026-01-01', verbose_name='Datum transakcije'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='transaction_type',
            field=models.CharField(choices=[('debit', 'Terećenje'), ('credit', 'Odobrenje')], default='credit', max_length=10, verbose_name='Tip'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='value_date',
            field=models.DateField(blank=True, null=True, verbose_name='Datum valute'),
        ),
        migrations.AlterField(
            model_name='banktransaction',
            name='bank_statement',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transactions', to='banking.bankstatement', verbose_name='Bankovni izvod'),
        ),
    ]
