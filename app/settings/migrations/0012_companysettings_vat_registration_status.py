from django.db import migrations, models


def backfill_vat_profile(apps, schema_editor):
    CompanySettings = apps.get_model('settings', 'CompanySettings')
    from shared.oib import is_valid_oib, normalize_oib

    for company in CompanySettings.objects.all():
        company.vat_registration_status = 'registered'
        oib = normalize_oib(company.vat_number or '')
        if is_valid_oib(oib):
            company.vat_id = f'HR{oib}'
        company.save(update_fields=['vat_registration_status', 'vat_id'])


def reverse_backfill(apps, schema_editor):
    CompanySettings = apps.get_model('settings', 'CompanySettings')
    CompanySettings.objects.update(vat_id='', vat_registration_status='registered')


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0011_seed_taxoffice_backfill_address'),
    ]

    operations = [
        migrations.AddField(
            model_name='companysettings',
            name='vat_id',
            field=models.CharField(blank=True, max_length=20, verbose_name='PDV identifikacijski broj'),
        ),
        migrations.AddField(
            model_name='companysettings',
            name='vat_registration_status',
            field=models.CharField(
                choices=[
                    ('none', 'Nije u sustavu PDV-a'),
                    ('vat_id', 'Nije u sustavu PDV-a, ima PDV ID (samo za EU stjecanja/usluge)'),
                    ('registered', 'U sustavu PDV-a'),
                ],
                default='registered',
                max_length=20,
                verbose_name='Status u sustavu PDV-a',
            ),
        ),
        migrations.AlterField(
            model_name='companysettings',
            name='vat_number',
            field=models.CharField(blank=True, max_length=20, verbose_name='OIB'),
        ),
        migrations.RunPython(backfill_vat_profile, reverse_backfill),
    ]
