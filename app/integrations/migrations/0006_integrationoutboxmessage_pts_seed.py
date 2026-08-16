# Generated migration — IntegrationOutboxMessage + PTS test tenant seed

from django.db import migrations, models
import django.db.models.deletion
import uuid


def seed_pts_test_tenant(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')
    DirectTenantConfig = apps.get_model('fiscal_gateway', 'DirectTenantConfig')
    FiscalTenantConfig = apps.get_model('fiscal_gateway', 'FiscalTenantConfig')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if tenant is None:
        return

    DirectTenantConfig.objects.update_or_create(
        tenant=tenant,
        defaults={
            'oib': '36619131370',
            'is_active': True,
        },
    )

    if FiscalTenantConfig.objects.filter(tenant=tenant, is_active=True).exists():
        IntegrationConfig.objects.update_or_create(
            tenant=tenant,
            integration_type='fiscalization',
            provider='cis',
            environment='test',
            defaults={
                'is_active': True,
                'display_name': 'CIS fiskalizacija (PTS test)',
            },
        )

    IntegrationConfig.objects.update_or_create(
        tenant=tenant,
        integration_type='eracun',
        provider='direct',
        environment='test',
        defaults={
            'is_active': True,
            'display_name': 'DIRECT AS4 (PTS test)',
        },
    )


def unseed_pts_test_tenant(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    IntegrationConfig = apps.get_model('integrations', 'IntegrationConfig')

    tenant = Tenant.objects.filter(slug='finestar').first()
    if tenant is None:
        return

    IntegrationConfig.objects.filter(
        tenant=tenant,
        environment='test',
        display_name__in=('CIS fiskalizacija (PTS test)', 'DIRECT AS4 (PTS test)'),
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0005_integrationauditlog'),
        ('fiscal_gateway', '0003_faza3_direct_as4'),
        ('invoices', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='IntegrationOutboxMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('correlation_id', models.UUIDField(db_index=True, default=uuid.uuid4)),
                ('provider', models.CharField(blank=True, choices=[('super', 'SUPER'), ('direct', 'Direktno (racunAI)'), ('mer', 'MER'), ('cis', 'CIS (Porezna)')], max_length=20)),
                ('operation', models.CharField(choices=[('send_eracun', 'Pošalji eRačun'), ('fiscalize', 'Fiskaliziraj'), ('app_response', 'ApplicationResponse')], max_length=30)),
                ('payload_ref', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('pending', 'Na čekanju'), ('retrying', 'Ponovni pokušaj'), ('succeeded', 'Uspješno'), ('dead', 'Dead letter')], db_index=True, default='pending', max_length=20)),
                ('attempt_count', models.PositiveSmallIntegerField(default=0)),
                ('max_attempts', models.PositiveSmallIntegerField(default=5)),
                ('next_retry_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('idempotency_key', models.CharField(max_length=200, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='integration_outbox_messages', to='invoices.invoice')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_set', to='tenants.tenant')),
            ],
            options={
                'verbose_name': 'Integracijski outbox',
                'verbose_name_plural': 'Integracijski outbox poruke',
                'ordering': ['-created_at'],
            },
        ),
        migrations.RunPython(seed_pts_test_tenant, unseed_pts_test_tenant),
    ]
