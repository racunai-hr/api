from django.db import migrations, models


def migrate_connection_statuses(apps, schema_editor):
    BankConnection = apps.get_model('banking', 'BankConnection')
    BankAccount = apps.get_model('payments', 'BankAccount')

    status_map = {
        'disconnected': 'pending',
        'active': 'connected',
    }
    for old, new in status_map.items():
        BankConnection.objects.filter(status=old).update(status=new)

    for connection in BankConnection.objects.exclude(external_account_id='').iterator():
        account = connection.bank_account
        if connection.external_account_id and not account.external_account_id:
            account.external_account_id = connection.external_account_id
            account.provider_connection_id = connection.pk
            if account.iban:
                account.status = 'active'
            account.save(
                update_fields=[
                    'external_account_id',
                    'provider_connection_id',
                    'status',
                ],
            )


def reverse_connection_statuses(apps, schema_editor):
    BankConnection = apps.get_model('banking', 'BankConnection')
    reverse_map = {
        'pending': 'disconnected',
        'authorizing': 'disconnected',
        'connected': 'active',
        'syncing': 'active',
        'revoked': 'disconnected',
        'expired': 'disconnected',
    }
    for new, old in reverse_map.items():
        BankConnection.objects.filter(status=new).update(status=old)


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0007_seed_bank_providers'),
        ('payments', '0005_psd2_bank_account'),
    ]

    operations = [
        migrations.AddField(
            model_name='bankconnection',
            name='sync_success_streak',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='bankconnection',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Na čekanju'),
                    ('authorizing', 'Autorizacija u tijeku'),
                    ('connected', 'Spojeno'),
                    ('syncing', 'Sinkronizacija'),
                    ('error', 'Greška'),
                    ('revoked', 'Opozvano'),
                    ('expired', 'Isteklo'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='bankconsent',
            name='expiry_notified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='bankconsent',
            name='expiry_warning_level',
            field=models.CharField(
                choices=[
                    ('none', 'Nema'),
                    ('14d', '14 dana'),
                    ('3d', '3 dana'),
                    ('expired', 'Istekao'),
                ],
                default='none',
                max_length=10,
            ),
        ),
        migrations.RunPython(migrate_connection_statuses, reverse_connection_statuses),
        migrations.RemoveField(
            model_name='bankconnection',
            name='external_account_id',
        ),
    ]
