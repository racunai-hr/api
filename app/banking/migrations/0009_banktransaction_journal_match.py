from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0006_vat_ledger_type_ira_ura'),
        ('banking', '0008_psd2_lifecycle'),
        ('payments', '0006_bankaccount_ledger_account'),
    ]

    operations = [
        migrations.AddField(
            model_name='banktransaction',
            name='matched_journal_entry',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='matched_bank_transactions',
                to='accounting.journalentry',
                verbose_name='Povezana temeljnica',
            ),
        ),
    ]
