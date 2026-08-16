# Generated manually — Faza 3 DIRECT eRačun models

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('fiscal_gateway', '0002_seed_finestar_fiscal_config'),
        ('tenants', '0007_create_finestar_tenant'),
    ]

    operations = [
        migrations.CreateModel(
            name='DirectTenantConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('oib', models.CharField(max_length=11, verbose_name='OIB pristupne točke')),
                ('ap_party_id', models.CharField(blank=True, help_text='Prazno = koristi DOMIBUS_AP_PARTY_ID iz okoline.', max_length=100, verbose_name='Domibus AP PartyId')),
                ('domibus_ws_url', models.CharField(blank=True, help_text='Prazno = koristi DOMIBUS_WS_URL iz okoline.', max_length=500, verbose_name='Domibus WS URL')),
                ('mps_service_url', models.CharField(blank=True, help_text='Prazno = koristi MPS_SERVICE_URL iz okoline.', max_length=500, verbose_name='MPS servis URL')),
                ('is_active', models.BooleanField(default=True, verbose_name='Aktivna DIRECT integracija')),
                ('last_outbound_at', models.DateTimeField(blank=True, null=True, verbose_name='Zadnji izlazni AS4')),
                ('last_inbound_at', models.DateTimeField(blank=True, null=True, verbose_name='Zadnji ulazni AS4')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'DIRECT eRačun konfiguracija',
                'verbose_name_plural': 'DIRECT eRačun konfiguracije',
            },
        ),
        migrations.CreateModel(
            name='As4DocumentLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('direction', models.CharField(choices=[('inbound', 'Ulazni'), ('outbound', 'Izlazni')], max_length=10)),
                ('message_id', models.CharField(db_index=True, max_length=200)),
                ('ref_message_id', models.CharField(blank=True, max_length=200)),
                ('as4_status', models.CharField(choices=[('sent', 'Poslan'), ('delivered', 'Dostavljen'), ('failed', 'Neuspješan'), ('accepted', 'Prihvaćen'), ('rejected', 'Odbijen')], default='sent', max_length=20)),
                ('ubl_xml', models.TextField(blank=True)),
                ('recipient_oib', models.CharField(blank=True, max_length=11)),
                ('supplier_oib', models.CharField(blank=True, max_length=11)),
                ('conversation_id', models.CharField(blank=True, max_length=200)),
                ('from_party_id', models.CharField(blank=True, max_length=200)),
                ('object_id', models.PositiveIntegerField()),
                ('last_synced_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'AS4 dokument',
                'verbose_name_plural': 'AS4 dokumenti',
            },
        ),
        migrations.AddConstraint(
            model_name='directtenantconfig',
            constraint=models.UniqueConstraint(fields=('tenant',), name='unique_direct_config_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='as4documentlink',
            constraint=models.UniqueConstraint(fields=('tenant', 'direction', 'message_id'), name='unique_as4_message_per_tenant_direction'),
        ),
    ]
