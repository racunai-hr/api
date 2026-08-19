from django.db import migrations, models


def normalize_existing_ibans(apps, schema_editor):
    PartnerBankAccount = apps.get_model('partners', 'PartnerBankAccount')
    for account in PartnerBankAccount.objects.all().iterator():
        normalized = (account.iban or '').replace(' ', '').upper()
        if account.iban != normalized:
            account.iban = normalized
            account.save(update_fields=['iban'])


class Migration(migrations.Migration):

    dependencies = [
        ('partners', '0004_tenant_required'),
    ]

    operations = [
        migrations.RunPython(normalize_existing_ibans, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='partnerbankaccount',
            constraint=models.UniqueConstraint(
                fields=('partner', 'iban'),
                name='unique_partner_iban',
            ),
        ),
    ]
