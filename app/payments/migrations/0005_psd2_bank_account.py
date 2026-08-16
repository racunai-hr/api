from django.db import migrations, models
import django.db.models.deletion


def set_existing_accounts_active(apps, schema_editor):
    BankAccount = apps.get_model('payments', 'BankAccount')
    BankAccount.objects.filter(status='').update(status='active')
    BankAccount.objects.filter(status__isnull=True).update(status='active')


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0007_seed_bank_providers'),
        ('payments', '0004_eur_default_currency'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='bankaccount',
            name='unique_iban_per_tenant',
        ),
        migrations.AddField(
            model_name='bankaccount',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending_discovery', 'Čeka otkrivanje'),
                    ('active', 'Aktivan'),
                    ('inactive', 'Neaktivan'),
                ],
                default='active',
                max_length=20,
                verbose_name='Status',
            ),
        ),
        migrations.AddField(
            model_name='bankaccount',
            name='external_account_id',
            field=models.CharField(blank=True, max_length=100, verbose_name='Vanjski ID računa'),
        ),
        migrations.AddField(
            model_name='bankaccount',
            name='discovered_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Otkriven'),
        ),
        migrations.AddField(
            model_name='bankaccount',
            name='provider_connection',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='discovered_accounts',
                to='banking.bankconnection',
                verbose_name='PSD2 veza',
            ),
        ),
        migrations.AlterField(
            model_name='bankaccount',
            name='iban',
            field=models.CharField(blank=True, max_length=34, null=True, verbose_name='IBAN'),
        ),
        migrations.AddConstraint(
            model_name='bankaccount',
            constraint=models.UniqueConstraint(
                condition=models.Q(iban__isnull=False) & ~models.Q(iban=''),
                fields=('tenant', 'iban'),
                name='unique_iban_per_tenant',
            ),
        ),
        migrations.RunPython(set_existing_accounts_active, migrations.RunPython.noop),
    ]
