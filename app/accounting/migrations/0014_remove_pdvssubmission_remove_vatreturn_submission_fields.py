# Generated manually for ADR-0008

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0013_submissionevent_pdvsreturn'),
    ]

    operations = [
        migrations.DeleteModel(
            name='PdvSSubmission',
        ),
        migrations.RemoveField(
            model_name='vatreturn',
            name='eporezna_identifier',
        ),
        migrations.RemoveField(
            model_name='vatreturn',
            name='submission_confirmation',
        ),
        migrations.RemoveField(
            model_name='vatreturn',
            name='submission_method',
        ),
        migrations.RemoveField(
            model_name='vatreturn',
            name='submitted_at',
        ),
        migrations.RemoveField(
            model_name='vatreturn',
            name='submitted_by',
        ),
        migrations.RemoveField(
            model_name='vatreturn',
            name='submitted_version',
        ),
    ]
