# Generated manually for accounting module expansion

import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0003_tenant_required'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('expenses', '0003_tenant_required'),
        ('partners', '0004_tenant_required'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RRIFChartEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=6, unique=True, verbose_name='RRIF ključ')),
                ('name', models.CharField(max_length=500, verbose_name='Naziv')),
                ('parent_code', models.CharField(blank=True, max_length=6, verbose_name='Nadređeni ključ')),
                ('account_class', models.CharField(max_length=1, verbose_name='Klasa')),
                ('account_type_name', models.CharField(max_length=20, verbose_name='Tip konta')),
                ('level', models.PositiveSmallIntegerField(default=0, verbose_name='Razina')),
                ('is_synthetic', models.BooleanField(default=False, verbose_name='Sintetički')),
                ('is_postable', models.BooleanField(default=True, verbose_name='Knjiživo')),
            ],
            options={
                'verbose_name': 'RRIF konto',
                'verbose_name_plural': 'RRIF kontni plan',
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='FiscalPeriod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveSmallIntegerField(verbose_name='Godina')),
                ('month', models.PositiveSmallIntegerField(verbose_name='Mjesec')),
                ('status', models.CharField(choices=[('open', 'Otvoreno'), ('closed', 'Zatvoreno')], default='open', max_length=10, verbose_name='Status')),
                ('closed_at', models.DateTimeField(blank=True, null=True, verbose_name='Datum zatvaranja')),
                ('closed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='closed_periods', to=settings.AUTH_USER_MODEL, verbose_name='Zatvorio')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Fiskalno razdoblje',
                'verbose_name_plural': 'Fiskalna razdoblja',
                'ordering': ['-year', '-month'],
            },
        ),
        migrations.CreateModel(
            name='VATPeriod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveSmallIntegerField(verbose_name='Godina')),
                ('month', models.PositiveSmallIntegerField(verbose_name='Mjesec')),
                ('status', models.CharField(choices=[('open', 'Otvoreno'), ('closed', 'Zatvoreno'), ('submitted', 'Predano')], default='open', max_length=10, verbose_name='Status')),
                ('submitted_at', models.DateTimeField(blank=True, null=True, verbose_name='Datum predaje')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'PDV razdoblje',
                'verbose_name_plural': 'PDV razdoblja',
                'ordering': ['-year', '-month'],
            },
        ),
        migrations.AddField(
            model_name='chartofaccounts',
            name='account_class',
            field=models.CharField(blank=True, max_length=1, verbose_name='RRIF klasa'),
        ),
        migrations.AddField(
            model_name='chartofaccounts',
            name='is_postable',
            field=models.BooleanField(default=True, verbose_name='Knjiživo'),
        ),
        migrations.AddField(
            model_name='chartofaccounts',
            name='is_rrif',
            field=models.BooleanField(default=False, verbose_name='RRIF konto'),
        ),
        migrations.AddField(
            model_name='chartofaccounts',
            name='is_synthetic',
            field=models.BooleanField(default=False, verbose_name='Sintetički'),
        ),
        migrations.AddField(
            model_name='chartofaccounts',
            name='level',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='Razina'),
        ),
        migrations.AddField(
            model_name='chartofaccounts',
            name='rrif_code',
            field=models.CharField(blank=True, max_length=6, verbose_name='RRIF ključ'),
        ),
        migrations.AlterField(
            model_name='chartofaccounts',
            name='account_code',
            field=models.CharField(max_length=20, verbose_name='Šifra konta'),
        ),
        migrations.AlterField(
            model_name='chartofaccounts',
            name='account_name',
            field=models.CharField(max_length=500, verbose_name='Naziv konta'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='is_auto',
            field=models.BooleanField(default=False, verbose_name='Automatsko knjiženje'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='source_object_id',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='ID izvora'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='fiscal_period',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='journal_entries', to='accounting.fiscalperiod', verbose_name='Fiskalno razdoblje'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='reversed_entry',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reversals', to='accounting.journalentry', verbose_name='Storno temeljnice'),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='source_content_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='contenttypes.contenttype', verbose_name='Tip izvora'),
        ),
        migrations.CreateModel(
            name='AnalyticAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('account_code', models.CharField(max_length=20, verbose_name='Analitička šifra')),
                ('account_name', models.CharField(max_length=500, verbose_name='Naziv')),
                ('counterparty_type', models.CharField(choices=[('partner', 'Partner (kupac)'), ('supplier', 'Dobavljač')], max_length=10, verbose_name='Tip subjekta')),
                ('is_active', models.BooleanField(default=True, verbose_name='Aktivan')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Datum stvaranja')),
                ('partner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='analytic_accounts', to='partners.partner', verbose_name='Partner')),
                ('supplier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='analytic_accounts', to='expenses.supplier', verbose_name='Dobavljač')),
                ('synthetic_account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='analytic_accounts', to='accounting.chartofaccounts', verbose_name='Sintetički konto')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
                ('chart_account', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='analytic_source', to='accounting.chartofaccounts', verbose_name='Konto u kontnom planu')),
            ],
            options={
                'verbose_name': 'Analitički konto',
                'verbose_name_plural': 'Analitički konti',
            },
        ),
        migrations.CreateModel(
            name='PostingRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='Naziv pravila')),
                ('document_type', models.CharField(choices=[('invoice_issued', 'Izdani račun'), ('invoice_paid', 'Naplata računa'), ('expense_approved', 'Odobren trošak'), ('expense_paid', 'Plaćen trošak'), ('payment_manual', 'Ručno plaćanje')], max_length=20, verbose_name='Tip dokumenta')),
                ('debit_account_code', models.CharField(max_length=20, verbose_name='Konto duguje (šifra)')),
                ('credit_account_code', models.CharField(max_length=20, verbose_name='Konto potražuje (šifra)')),
                ('amount_field', models.CharField(choices=[('subtotal', 'Osnovica'), ('tax_amount', 'PDV'), ('total_amount', 'Ukupan iznos'), ('amount', 'Iznos'), ('net_amount', 'Neto iznos')], max_length=20, verbose_name='Polje iznosa')),
                ('condition', models.JSONField(blank=True, default=dict, verbose_name='Uvjet')),
                ('priority', models.PositiveSmallIntegerField(default=100, verbose_name='Prioritet')),
                ('is_active', models.BooleanField(default=True, verbose_name='Aktivno')),
                ('use_analytic', models.BooleanField(default=False, verbose_name='Koristi analitiku')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Pravilo knjiženja',
                'verbose_name_plural': 'Pravila knjiženja',
                'ordering': ['document_type', 'priority'],
            },
        ),
        migrations.CreateModel(
            name='VATLedgerEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ledger_type', models.CharField(choices=[('U-RA', 'U-RA (izlazni)'), ('K-RA', 'K-RA (ulazni)'), ('IRA', 'IRA')], max_length=5, verbose_name='Knjiga')),
                ('entry_date', models.DateField(verbose_name='Datum')),
                ('document_number', models.CharField(max_length=50, verbose_name='Broj dokumenta')),
                ('partner_name', models.CharField(max_length=200, verbose_name='Partner')),
                ('partner_oib', models.CharField(blank=True, max_length=20, verbose_name='OIB')),
                ('base_amount', models.DecimalField(decimal_places=2, max_digits=15, verbose_name='Osnovica')),
                ('vat_rate', models.DecimalField(decimal_places=2, max_digits=5, verbose_name='Stopa PDV (%)')),
                ('vat_amount', models.DecimalField(decimal_places=2, max_digits=15, verbose_name='Iznos PDV')),
                ('rrif_vat_account', models.CharField(blank=True, max_length=10, verbose_name='RRIF PDV konto')),
                ('source_object_id', models.PositiveIntegerField(blank=True, null=True)),
                ('source_content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='contenttypes.contenttype')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
                ('vat_period', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='accounting.vatperiod', verbose_name='PDV razdoblje')),
            ],
            options={
                'verbose_name': 'Stavka PDV knjige',
                'verbose_name_plural': 'Stavke PDV knjiga',
                'ordering': ['entry_date', 'document_number'],
            },
        ),
        migrations.AddField(
            model_name='journalentryline',
            name='analytic_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='journal_lines', to='accounting.analyticaccount', verbose_name='Analitički konto'),
        ),
        migrations.AddConstraint(
            model_name='fiscalperiod',
            constraint=models.UniqueConstraint(fields=('tenant', 'year', 'month'), name='unique_fiscal_period_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='vatperiod',
            constraint=models.UniqueConstraint(fields=('tenant', 'year', 'month'), name='unique_vat_period_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='analyticaccount',
            constraint=models.UniqueConstraint(fields=('tenant', 'account_code'), name='unique_analytic_code_per_tenant'),
        ),
    ]
