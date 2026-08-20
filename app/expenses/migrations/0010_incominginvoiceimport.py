import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import expenses.models
import expenses.validators


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('expenses', '0009_expense_vat_procedure'),
        ('partners', '0006_partnerbankaccount_optional_bank_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='IncomingInvoiceImport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('import_uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('original_file', models.FileField(
                    upload_to=expenses.models.incoming_invoice_upload_to,
                    validators=[
                        expenses.validators.invoice_file_extension_validator,
                        expenses.validators.validate_invoice_file_size,
                        expenses.validators.validate_invoice_file_content,
                    ],
                    verbose_name='Originalni račun',
                )),
                ('original_filename', models.CharField(max_length=255, verbose_name='Originalni naziv datoteke')),
                ('content_type', models.CharField(blank=True, max_length=100)),
                ('file_sha256', models.CharField(db_index=True, max_length=64)),
                ('file_size', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(
                    choices=[
                        ('queued', 'U redu'),
                        ('processing', 'Obrada'),
                        ('extracted', 'Ekstrahirano'),
                        ('failed', 'Neuspjelo'),
                        ('confirmed', 'Potvrđeno'),
                        ('discarded', 'Odbačeno'),
                    ],
                    db_index=True,
                    default='queued',
                    max_length=20,
                )),
                ('idempotency_key', models.CharField(max_length=128)),
                ('celery_task_id', models.CharField(blank=True, default='', max_length=255)),
                ('last_error', models.CharField(blank=True, default='', max_length=500)),
                ('ocr_provider', models.CharField(blank=True, default='', max_length=40)),
                ('ocr_model', models.CharField(blank=True, default='', max_length=80)),
                ('ocr_schema_version', models.CharField(blank=True, default='', max_length=80)),
                ('ocr_extracted_at', models.DateTimeField(blank=True, null=True)),
                ('extracted_payload', models.JSONField(blank=True, default=dict)),
                ('warnings', models.JSONField(blank=True, default=list)),
                ('partner_match', models.CharField(
                    choices=[
                        ('exact_oib', 'Točan OIB'),
                        ('vat', 'PDV broj'),
                        ('iban_candidate', 'IBAN kandidat'),
                        ('missing', 'Nije pronađen'),
                    ],
                    default='missing',
                    max_length=20,
                )),
                ('partner_diff', models.JSONField(blank=True, default=list)),
                ('partner_candidate_id', models.IntegerField(blank=True, null=True)),
                ('duplicate_kind', models.CharField(
                    choices=[
                        ('none', 'Nema'),
                        ('hard', 'Hard'),
                        ('business', 'Poslovni kandidat'),
                    ],
                    default='none',
                    max_length=20,
                )),
                ('duplicate_detail', models.JSONField(blank=True, default=dict)),
                ('duplicate_override', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('confirmed_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='confirmed_incoming_invoice_imports',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Potvrdio',
                )),
                ('confirmed_expense', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='ocr_imports',
                    to='expenses.expense',
                    verbose_name='Potvrđeni trošak',
                )),
                ('duplicate_expense', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='ocr_duplicate_imports',
                    to='expenses.expense',
                    verbose_name='Duplikat troška',
                )),
                ('matched_partner', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='incoming_invoice_imports',
                    to='partners.partner',
                    verbose_name='Spojeni partner',
                )),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='%(app_label)s_%(class)s_set',
                    to='tenants.tenant',
                    verbose_name='Tenant',
                )),
                ('uploaded_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='incoming_invoice_imports',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Učitao',
                )),
            ],
            options={
                'verbose_name': 'OCR uvoz ulaznog računa',
                'verbose_name_plural': 'OCR uvozi ulaznih računa',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='incominginvoiceimport',
            index=models.Index(fields=['tenant', 'status', '-created_at'], name='ocrimport_tenant_status'),
        ),
        migrations.AddIndex(
            model_name='incominginvoiceimport',
            index=models.Index(fields=['tenant', 'file_sha256'], name='ocrimport_tenant_sha'),
        ),
        migrations.AddConstraint(
            model_name='incominginvoiceimport',
            constraint=models.UniqueConstraint(
                fields=('tenant', 'idempotency_key'),
                name='unique_incoming_invoice_import_idempotency',
            ),
        ),
    ]
