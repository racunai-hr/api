from django.db import migrations, models


def forwards_ledger_types(apps, schema_editor):
    VATLedgerEntry = apps.get_model('accounting', 'VATLedgerEntry')
    # Redoslijed je bitan: prvo izlazni U-RA → I-RA, zatim ulazni K-RA → U-RA.
    VATLedgerEntry.objects.filter(ledger_type='U-RA').update(ledger_type='I-RA')
    VATLedgerEntry.objects.filter(ledger_type='K-RA').update(ledger_type='U-RA')


def backwards_ledger_types(apps, schema_editor):
    VATLedgerEntry = apps.get_model('accounting', 'VATLedgerEntry')
    # Obrnuti redoslijed: prvo ulazni U-RA → K-RA, zatim izlazni I-RA → U-RA.
    VATLedgerEntry.objects.filter(ledger_type='U-RA').update(ledger_type='K-RA')
    VATLedgerEntry.objects.filter(ledger_type='I-RA').update(ledger_type='U-RA')


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0005_analytic_expense_payer'),
    ]

    operations = [
        migrations.RunPython(forwards_ledger_types, backwards_ledger_types),
        migrations.AlterField(
            model_name='vatledgerentry',
            name='ledger_type',
            field=models.CharField(
                choices=[
                    ('I-RA', 'I-RA (izlazni)'),
                    ('U-RA', 'U-RA (ulazni)'),
                    ('IRA', 'IRA (zastarjelo)'),
                ],
                max_length=5,
                verbose_name='Knjiga',
            ),
        ),
    ]
