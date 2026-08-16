# Generated manually for fiscal_gateway initial models

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('invoices', '0001_initial'),
        ('tenants', '0007_create_finestar_tenant'),
    ]

    operations = [
        migrations.CreateModel(
            name='FiscalTenantConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('oib', models.CharField(max_length=11, verbose_name='OIB obveznika')),
                ('p12_path', models.CharField(blank=True, help_text='Prazno = koristi FISCAL_CERT_P12_PATH iz okoline.', max_length=500, verbose_name='Putanja .p12')),
                ('p12_password', models.CharField(blank=True, help_text='Prazno = koristi FISCAL_CERT_P12_PASSWORD iz okoline.', max_length=200, verbose_name='Lozinka .p12')),
                ('cis_env', models.CharField(choices=[('demo', 'CIS demo (test)'), ('prod', 'CIS produkcija')], default='demo', max_length=10, verbose_name='CIS okolina')),
                ('certificate_label', models.CharField(blank=True, help_text='npr. FISKAL 2', max_length=100, verbose_name='Oznaka certifikata')),
                ('is_active', models.BooleanField(default=True, verbose_name='Aktivna fiskalizacija')),
                ('last_ping_at', models.DateTimeField(blank=True, null=True, verbose_name='Zadnji ping')),
                ('last_submission_at', models.DateTimeField(blank=True, null=True, verbose_name='Zadnja fiskalizacija')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Fiskalna konfiguracija',
                'verbose_name_plural': 'Fiskalne konfiguracije',
            },
        ),
        migrations.CreateModel(
            name='FiscalSubmissionLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operation', models.CharField(choices=[('evidentiraj_eracun', 'Evidentiraj eRačun')], max_length=40)),
                ('request_id', models.CharField(default=uuid.uuid4, max_length=64, unique=True)),
                ('xml_hash', models.CharField(blank=True, max_length=64)),
                ('request_xml', models.TextField(blank=True)),
                ('response_xml', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('pending', 'U tijeku'), ('success', 'Uspješno'), ('rejected', 'Odbijeno (CIS)'), ('error', 'Greška')], default='pending', max_length=20)),
                ('cis_request_id', models.CharField(blank=True, max_length=64, verbose_name='CIS idZahtjeva')),
                ('jir', models.CharField(blank=True, max_length=64, verbose_name='JIR')),
                ('error_code', models.CharField(blank=True, max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fiscal_submissions', to='invoices.invoice')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Fiskalni log slanja',
                'verbose_name_plural': 'Fiskalni logovi slanja',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='fiscaltenantconfig',
            constraint=models.UniqueConstraint(fields=('tenant',), name='unique_fiscal_config_per_tenant'),
        ),
    ]
