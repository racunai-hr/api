# Generated manually for ADR-0008

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import accounting.models


def _map_submission_method(method: str) -> str:
    return {'manual_ep': 'manual', 'api': 'api', 'import': 'import'}.get(method or '', 'manual')


def backfill_submission_events(apps, schema_editor):
    VATReturn = apps.get_model('accounting', 'VATReturn')
    PdvSSubmission = apps.get_model('accounting', 'PdvSSubmission')
    PDVSReturn = apps.get_model('accounting', 'PDVSReturn')
    SubmissionEvent = apps.get_model('accounting', 'SubmissionEvent')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    vat_return_ct, _ = ContentType.objects.get_or_create(
        app_label='accounting',
        model='vatreturn',
    )
    pdvs_return_ct, _ = ContentType.objects.get_or_create(
        app_label='accounting',
        model='pdvsreturn',
    )

    for vat_return in VATReturn.objects.filter(status__in=('submitted', 'imported')):
        if not vat_return.submitted_at or not vat_return.eporezna_identifier:
            continue
        SubmissionEvent.objects.create(
            tenant_id=vat_return.tenant_id,
            document_type='pdv',
            content_type=vat_return_ct,
            object_id=vat_return.pk,
            submission_no=1,
            state='submitted',
            destination='eporezna',
            external_identifier=vat_return.eporezna_identifier,
            submitted_at=vat_return.submitted_at,
            submitted_by_id=vat_return.submitted_by_id,
            submission_method=_map_submission_method(vat_return.submission_method),
            confirmation_attachment=vat_return.submission_confirmation,
        )

    for pdv_s_sub in PdvSSubmission.objects.all():
        pdvs_return, _ = PDVSReturn.objects.get_or_create(
            tenant_id=pdv_s_sub.tenant_id,
            vat_period_id=pdv_s_sub.vat_period_id,
            defaults={'version': 1},
        )
        SubmissionEvent.objects.create(
            tenant_id=pdv_s_sub.tenant_id,
            document_type='pdv_s',
            content_type=pdvs_return_ct,
            object_id=pdvs_return.pk,
            submission_no=1,
            state='submitted',
            destination='eporezna',
            external_identifier=pdv_s_sub.eporezna_identifier,
            submitted_at=pdv_s_sub.submitted_at,
            submitted_by_id=pdv_s_sub.submitted_by_id,
            submission_method=_map_submission_method(pdv_s_sub.submission_method),
            confirmation_attachment=pdv_s_sub.submission_confirmation,
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contenttypes', '0002_remove_content_type_name'),
        ('accounting', '0012_vatreturn_submitted_version'),
    ]

    operations = [
        migrations.CreateModel(
            name='PDVSReturn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveSmallIntegerField(default=1, verbose_name='Verzija')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')),
                (
                    'tenant',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant'),
                ),
                (
                    'vat_period',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='pdv_s_return',
                        to='accounting.vatperiod',
                        verbose_name='PDV razdoblje',
                    ),
                ),
            ],
            options={
                'verbose_name': 'PDV-S obrazac',
                'verbose_name_plural': 'PDV-S obrasci',
            },
        ),
        migrations.AddConstraint(
            model_name='pdvsreturn',
            constraint=models.UniqueConstraint(
                fields=('tenant', 'vat_period'),
                name='unique_pdv_s_return_per_period',
            ),
        ),
        migrations.CreateModel(
            name='SubmissionEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'document_type',
                    models.CharField(
                        choices=[('pdv', 'Obrazac PDV'), ('pdv_s', 'Obrazac PDV-S')],
                        max_length=16,
                        verbose_name='Tip dokumenta',
                    ),
                ),
                ('object_id', models.PositiveBigIntegerField(verbose_name='ID dokumenta')),
                ('submission_no', models.PositiveSmallIntegerField(verbose_name='Broj predaje')),
                (
                    'state',
                    models.CharField(
                        choices=[
                            ('pending', 'Na čekanju'),
                            ('submitted', 'Predano'),
                            ('rejected', 'Odbijeno'),
                            ('cancelled', 'Otkazano'),
                        ],
                        default='submitted',
                        max_length=12,
                        verbose_name='Status',
                    ),
                ),
                (
                    'destination',
                    models.CharField(
                        choices=[
                            ('eporezna', 'ePorezna'),
                            ('mojeporezna', 'Moje porezna'),
                            ('fiskalizacija', 'Fiskalizacija'),
                            ('hzzo', 'HZZO'),
                            ('test', 'Test'),
                        ],
                        max_length=16,
                        verbose_name='Odredište',
                    ),
                ),
                ('external_identifier', models.UUIDField(verbose_name='Vanjski identifikator')),
                ('submitted_at', models.DateTimeField(verbose_name='Datum predaje')),
                (
                    'submission_method',
                    models.CharField(
                        choices=[('manual', 'Ručno'), ('api', 'API'), ('import', 'Import')],
                        max_length=8,
                        verbose_name='Način predaje',
                    ),
                ),
                (
                    'confirmation_attachment',
                    models.FileField(
                        blank=True,
                        null=True,
                        upload_to=accounting.models.submission_event_upload_to,
                        verbose_name='Potvrda predaje',
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')),
                (
                    'content_type',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to='contenttypes.contenttype',
                        verbose_name='Tip sadržaja',
                    ),
                ),
                (
                    'submitted_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='submission_events',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Predao',
                    ),
                ),
                (
                    'supersedes_submission',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='superseded_by_events',
                        to='accounting.submissionevent',
                        verbose_name='Zamjenjuje predaju',
                    ),
                ),
                (
                    'tenant',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant'),
                ),
            ],
            options={
                'verbose_name': 'Evidencija predaje',
                'verbose_name_plural': 'Evidencije predaje',
                'ordering': ['-submitted_at', '-submission_no'],
            },
        ),
        migrations.AddConstraint(
            model_name='submissionevent',
            constraint=models.UniqueConstraint(
                fields=('tenant', 'content_type', 'object_id', 'submission_no'),
                name='unique_submission_no_per_document',
            ),
        ),
        migrations.AddConstraint(
            model_name='submissionevent',
            constraint=models.UniqueConstraint(
                fields=('tenant', 'destination', 'external_identifier'),
                name='unique_external_id_per_destination',
            ),
        ),
        migrations.RunPython(backfill_submission_events, migrations.RunPython.noop),
    ]
