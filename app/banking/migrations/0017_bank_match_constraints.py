# Slice 2 / ADR-0021: fail-closed match uniqueness + at-most-one target.

from django.db import migrations, models


def assert_no_duplicate_bank_matches(apps, schema_editor):
    BankTransaction = apps.get_model('banking', 'BankTransaction')
    from banking.services.match_duplicates import raise_if_duplicate_bank_matches

    raise_if_duplicate_bank_matches(BankTransaction.objects.all())


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0016_bank_import_sync_runs'),
    ]

    operations = [
        migrations.RunPython(assert_no_duplicate_bank_matches, noop_reverse),
        migrations.AddConstraint(
            model_name='banktransaction',
            constraint=models.UniqueConstraint(
                condition=models.Q(matched_payment__isnull=False),
                fields=('matched_payment',),
                name='unique_banktx_matched_payment',
            ),
        ),
        migrations.AddConstraint(
            model_name='banktransaction',
            constraint=models.UniqueConstraint(
                condition=models.Q(matched_journal_entry__isnull=False),
                fields=('matched_journal_entry',),
                name='unique_banktx_matched_journal',
            ),
        ),
        migrations.AddConstraint(
            model_name='banktransaction',
            constraint=models.CheckConstraint(
                check=(
                    models.Q(matched_payment__isnull=True)
                    | models.Q(matched_journal_entry__isnull=True)
                ),
                name='banktx_at_most_one_match_target',
            ),
        ),
    ]
