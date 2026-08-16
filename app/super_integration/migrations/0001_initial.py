# Generated manually for super_integration initial models

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('tenants', '0007_create_finestar_tenant'),
    ]

    operations = [
        migrations.CreateModel(
            name='SuperTenantConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('api_base_url', models.URLField(default='https://apitest.super.hr', verbose_name='SUPER API URL')),
                ('username', models.CharField(max_length=200, verbose_name='SUPER korisničko ime')),
                ('password', models.CharField(max_length=200, verbose_name='SUPER lozinka')),
                ('company_guid', models.CharField(max_length=64, verbose_name='SUPER CompanyGuid')),
                ('is_active', models.BooleanField(default=True, verbose_name='Aktivna integracija')),
                ('is_test_mode', models.BooleanField(default=True, verbose_name='Test okruženje')),
                ('last_inbound_sync_at', models.DateTimeField(blank=True, null=True, verbose_name='Zadnji sync ulaznih')),
                ('last_outbound_poll_at', models.DateTimeField(blank=True, null=True, verbose_name='Zadnji poll izlaznih')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'SUPER konfiguracija',
                'verbose_name_plural': 'SUPER konfiguracije',
            },
        ),
        migrations.CreateModel(
            name='SuperDocumentLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('direction', models.CharField(choices=[('inbound', 'Ulazni'), ('outbound', 'Izlazni')], max_length=10)),
                ('super_guid', models.CharField(db_index=True, max_length=64)),
                ('super_unique_id', models.CharField(blank=True, max_length=32)),
                ('super_status', models.IntegerField(blank=True, null=True)),
                ('status_remark', models.TextField(blank=True)),
                ('ubl_xml', models.TextField(blank=True)),
                ('pdf_path', models.CharField(blank=True, max_length=500)),
                ('object_id', models.PositiveIntegerField()),
                ('last_synced_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='tenants.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'SUPER dokument',
                'verbose_name_plural': 'SUPER dokumenti',
            },
        ),
        migrations.AddConstraint(
            model_name='supertenantconfig',
            constraint=models.UniqueConstraint(fields=('tenant',), name='unique_super_config_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='superdocumentlink',
            constraint=models.UniqueConstraint(fields=('tenant', 'direction', 'super_guid'), name='unique_super_guid_per_tenant_direction'),
        ),
    ]
