from django.db import migrations, models
import django.db.models.deletion


def set_default_ledger_accounts(apps, schema_editor):
    BankAccount = apps.get_model('payments', 'BankAccount')
    ChartOfAccounts = apps.get_model('accounting', 'ChartOfAccounts')
    for bank_account in BankAccount.objects.filter(ledger_account__isnull=True):
        ledger = ChartOfAccounts.objects.filter(
            tenant_id=bank_account.tenant_id,
            account_code='1000',
        ).first()
        if ledger:
            bank_account.ledger_account_id = ledger.id
            bank_account.save(update_fields=['ledger_account_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0006_vat_ledger_type_ira_ura'),
        ('payments', '0005_psd2_bank_account'),
    ]

    operations = [
        migrations.AddField(
            model_name='bankaccount',
            name='ledger_account',
            field=models.ForeignKey(
                blank=True,
                help_text='Transakcijski konto u RRiF-u (npr. 1000). Ako nije postavljeno, koristi se 1000.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='bank_accounts',
                to='accounting.chartofaccounts',
                verbose_name='Konto u knjizi',
            ),
        ),
        migrations.RunPython(set_default_ledger_accounts, migrations.RunPython.noop),
    ]
