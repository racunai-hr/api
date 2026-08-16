import re

from django.db import migrations


FINE_STAR_VAT = '36619131370'
TAX_OFFICE_3566 = {
    'code': '3566',
    'name': 'Porezna uprava, Područni ured',
    'city': 'Šibenik',
}


def _parse_legacy_address(address: str) -> dict[str, str]:
    lines = [line.strip() for line in (address or '').split('\n') if line.strip()]
    street = ''
    house_number = ''
    postal_code = ''
    city = ''

    if lines:
        match = re.match(r'^(.+?)\s+(\d+[A-Za-z]?)$', lines[0])
        if match:
            street, house_number = match.group(1), match.group(2)
        else:
            street = lines[0]

    if len(lines) > 1:
        match = re.match(r'^(\d{5})\s+(.+)$', lines[1])
        if match:
            postal_code, city = match.group(1), match.group(2)
        else:
            city = lines[1]

    return {
        'street': street,
        'house_number': house_number,
        'postal_code': postal_code,
        'city': city,
        'country': 'HR',
    }


def seed_tax_office_and_backfill_addresses(apps, schema_editor):
    TaxOffice = apps.get_model('settings', 'TaxOffice')
    CompanySettings = apps.get_model('settings', 'CompanySettings')

    tax_office, _ = TaxOffice.objects.get_or_create(
        code=TAX_OFFICE_3566['code'],
        defaults={
            'name': TAX_OFFICE_3566['name'],
            'city': TAX_OFFICE_3566['city'],
        },
    )

    for company in CompanySettings.objects.all():
        updates = {}
        if not company.street and company.company_address:
            parsed = _parse_legacy_address(company.company_address)
            updates.update(parsed)

        if company.vat_number == FINE_STAR_VAT or (
            company.vat_number == '' and 'Fine Star' in (company.company_name or '')
        ):
            updates.setdefault('street', 'Bana Josipa Jelačića')
            updates.setdefault('house_number', '58')
            updates.setdefault('postal_code', '22000')
            updates.setdefault('city', 'Šibenik')
            updates.setdefault('country', 'HR')
            if not company.tax_office_id:
                updates['tax_office'] = tax_office

        if updates:
            for field, value in updates.items():
                setattr(company, field, value)
            company.save(update_fields=list(updates.keys()))


def reverse_seed(apps, schema_editor):
    TaxOffice = apps.get_model('settings', 'TaxOffice')
    CompanySettings = apps.get_model('settings', 'CompanySettings')

    CompanySettings.objects.update(
        street='',
        house_number='',
        postal_code='',
        city='',
        country='HR',
        tax_office=None,
    )
    TaxOffice.objects.filter(code=TAX_OFFICE_3566['code']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0010_taxoffice_companysettings_address'),
    ]

    operations = [
        migrations.RunPython(seed_tax_office_and_backfill_addresses, reverse_seed),
    ]
