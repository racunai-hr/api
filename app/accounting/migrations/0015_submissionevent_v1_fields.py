# Generated manually for ADR-0009

import uuid

from django.db import migrations, models


def _map_submission_method_to_source(method: str) -> str:
    return {
        'manual': 'manual',
        'api': 'api',
        'import': 'import',
    }.get(method or '', 'manual')


def backfill_submission_v1_fields(apps, schema_editor):
    SubmissionEvent = apps.get_model('accounting', 'SubmissionEvent')
    VATReturn = apps.get_model('accounting', 'VATReturn')

    vat_returns = {
        row.pk: row.payload_hash
        for row in VATReturn.objects.exclude(payload_hash='').only('pk', 'payload_hash')
    }

    for event in SubmissionEvent.objects.all():
        updated = False
        if not event.event_uuid:
            event.event_uuid = uuid.uuid4()
            updated = True
        if not event.source:
            event.source = _map_submission_method_to_source(event.submission_method)
            updated = True
        if not event.payload_hash and event.content_type.model == 'vatreturn':
            doc_hash = vat_returns.get(event.object_id, '')
            if doc_hash:
                event.payload_hash = doc_hash
                updated = True
        if updated:
            event.save(update_fields=['event_uuid', 'source', 'payload_hash'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0014_remove_pdvssubmission_remove_vatreturn_submission_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='submissionevent',
            name='event_uuid',
            field=models.UUIDField(editable=False, null=True, verbose_name='Javni ID eventa'),
        ),
        migrations.AddField(
            model_name='submissionevent',
            name='payload_hash',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='Hash sadržaja predaje'),
        ),
        migrations.AddField(
            model_name='submissionevent',
            name='source',
            field=models.CharField(
                blank=True,
                choices=[
                    ('manual', 'Ručno'),
                    ('api', 'API'),
                    ('migration', 'Migracija'),
                    ('import', 'Import'),
                    ('system', 'Sustav'),
                ],
                max_length=10,
                verbose_name='Izvor zapisa',
            ),
        ),
        migrations.AlterField(
            model_name='submissionevent',
            name='destination',
            field=models.CharField(
                choices=[
                    ('eporezna', 'ePorezna'),
                    ('hzzo', 'HZZO'),
                    ('fina', 'FINA'),
                    ('mojeporezna', 'Moje porezna'),
                    ('fiskalizacija', 'Fiskalizacija'),
                    ('test', 'Test'),
                ],
                max_length=16,
                verbose_name='Odredište',
            ),
        ),
        migrations.RunPython(backfill_submission_v1_fields, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='submissionevent',
            name='document_type',
        ),
        migrations.RemoveField(
            model_name='submissionevent',
            name='submission_method',
        ),
        migrations.AlterField(
            model_name='submissionevent',
            name='event_uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='Javni ID eventa'),
        ),
        migrations.AlterField(
            model_name='submissionevent',
            name='source',
            field=models.CharField(
                choices=[
                    ('manual', 'Ručno'),
                    ('api', 'API'),
                    ('migration', 'Migracija'),
                    ('import', 'Import'),
                    ('system', 'Sustav'),
                ],
                max_length=10,
                verbose_name='Izvor zapisa',
            ),
        ),
    ]
