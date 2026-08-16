# Generated manually — Faza 3 IntegrationAuditLog

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fiscal_gateway', '0003_faza3_direct_as4'),
        ('invoices', '0001_initial'),
        ('integrations', '0004_super_test_mode_to_environment'),
        ('super_integration', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tenants', '0007_create_finestar_tenant'),
    ]

    operations = [
        migrations.CreateModel(
            name='IntegrationAuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('correlation_id', models.UUIDField(db_index=True, default=uuid.uuid4)),
                ('integration_type', models.CharField(blank=True, choices=[('eracun', 'eRačun'), ('fiscalization', 'Fiskalizacija'), ('payment', 'Plaćanje'), ('shipping', 'Dostava')], max_length=20)),
                ('provider', models.CharField(blank=True, choices=[('super', 'SUPER'), ('direct', 'Direktno (racunAI)'), ('mer', 'MER'), ('cis', 'CIS (Porezna)')], max_length=20)),
                ('step', models.CharField(choices=[('document_built', 'Dokument izgrađen'), ('semantic_ok', 'Semantička validacija OK'), ('xsd_ok', 'XSD validacija OK'), ('schematron_ok', 'Schematron OK'), ('schematron_skipped', 'Schematron preskočen'), ('signed', 'UBL potpis'), ('outbound_sent', 'Izlazni poslan'), ('outbound_failed', 'Izlazni neuspješan'), ('fiscalized', 'Fiskaliziran'), ('fiscal_failed', 'Fiskalizacija neuspješna')], max_length=30)),
                ('status', models.CharField(choices=[('success', 'Uspjeh'), ('failed', 'Greška'), ('skipped', 'Preskočeno')], max_length=10)),
                ('detail', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('as4_link', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_logs', to='fiscal_gateway.as4documentlink')),
                ('fiscal_log', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_logs', to='fiscal_gateway.fiscalsubmissionlog')),
                ('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='integration_audit_logs', to='invoices.invoice')),
                ('super_link', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_logs', to='super_integration.superdocumentlink')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
                ('triggered_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='integration_audit_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Integracijski audit log',
                'verbose_name_plural': 'Integracijski audit logovi',
                'ordering': ['-created_at'],
            },
        ),
    ]
