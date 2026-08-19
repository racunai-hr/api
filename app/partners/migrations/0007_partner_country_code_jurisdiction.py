"""ADR-0023 step 1: add nullable country_code and backfill from legacy country."""

import logging

from django.db import migrations, models

logger = logging.getLogger(__name__)

_LEGACY_ALIASES = {
    'hr': 'HR',
    'hrvatska': 'HR',
    'croatia': 'HR',
    'republic of croatia': 'HR',
    'de': 'DE',
    'deutschland': 'DE',
    'germany': 'DE',
    'njemačka': 'DE',
    'njemacka': 'DE',
    'at': 'AT',
    'austria': 'AT',
    'österreich': 'AT',
    'osterreich': 'AT',
    'austrija': 'AT',
    'si': 'SI',
    'slovenia': 'SI',
    'slovenija': 'SI',
    'it': 'IT',
    'italy': 'IT',
    'italia': 'IT',
    'italija': 'IT',
    'hu': 'HU',
    'hungary': 'HU',
    'mađarska': 'HU',
    'madjarska': 'HU',
    'fr': 'FR',
    'france': 'FR',
    'francuska': 'FR',
    'be': 'BE',
    'belgium': 'BE',
    'belgija': 'BE',
    'nl': 'NL',
    'netherlands': 'NL',
    'nizozemska': 'NL',
    'pl': 'PL',
    'poland': 'PL',
    'poljska': 'PL',
    'cz': 'CZ',
    'czech republic': 'CZ',
    'czechia': 'CZ',
    'češka': 'CZ',
    'ceska': 'CZ',
    'sk': 'SK',
    'slovakia': 'SK',
    'slovačka': 'SK',
    'slovacka': 'SK',
    'es': 'ES',
    'spain': 'ES',
    'spanjolska': 'ES',
    'pt': 'PT',
    'portugal': 'PT',
    'gr': 'GR',
    'greece': 'GR',
    'grčka': 'GR',
    'grcka': 'GR',
    'ro': 'RO',
    'romania': 'RO',
    'rumunjska': 'RO',
    'bg': 'BG',
    'bulgaria': 'BG',
    'bugarska': 'BG',
    'se': 'SE',
    'sweden': 'SE',
    'švedska': 'SE',
    'svedska': 'SE',
    'dk': 'DK',
    'denmark': 'DK',
    'danska': 'DK',
    'fi': 'FI',
    'finland': 'FI',
    'finska': 'FI',
    'ee': 'EE',
    'estonia': 'EE',
    'estonija': 'EE',
    'lv': 'LV',
    'latvia': 'LV',
    'latvija': 'LV',
    'lt': 'LT',
    'lithuania': 'LT',
    'litva': 'LT',
    'ie': 'IE',
    'ireland': 'IE',
    'irska': 'IE',
    'lu': 'LU',
    'luxembourg': 'LU',
    'luksemburg': 'LU',
    'mt': 'MT',
    'malta': 'MT',
    'cy': 'CY',
    'cyprus': 'CY',
    'cipar': 'CY',
    'gb': 'GB',
    'uk': 'GB',
    'united kingdom': 'GB',
    'ujedinjeno kraljevstvo': 'GB',
    'us': 'US',
    'usa': 'US',
    'united states': 'US',
    'ch': 'CH',
    'switzerland': 'CH',
    'švicarska': 'CH',
    'svicarska': 'CH',
    'rs': 'RS',
    'serbia': 'RS',
    'srbija': 'RS',
    'ba': 'BA',
    'bosnia and herzegovina': 'BA',
    'bosna i hercegovina': 'BA',
    'me': 'ME',
    'montenegro': 'ME',
    'crna gora': 'ME',
}

_DISPLAY = {
    'HR': 'Hrvatska',
    'DE': 'Njemačka',
    'AT': 'Austrija',
    'SI': 'Slovenija',
    'IT': 'Italija',
    'HU': 'Mađarska',
    'FR': 'Francuska',
    'BE': 'Belgija',
    'NL': 'Nizozemska',
    'PL': 'Poljska',
    'CZ': 'Češka',
    'SK': 'Slovačka',
    'ES': 'Španjolska',
    'PT': 'Portugal',
    'GR': 'Grčka',
    'RO': 'Rumunjska',
    'BG': 'Bugarska',
    'SE': 'Švedska',
    'DK': 'Danska',
    'FI': 'Finska',
    'EE': 'Estonija',
    'LV': 'Latvija',
    'LT': 'Litva',
    'IE': 'Irska',
    'LU': 'Luksemburg',
    'MT': 'Malta',
    'CY': 'Cipar',
    'GB': 'Ujedinjeno Kraljevstvo',
    'US': 'Sjedinjene Američke Države',
    'CH': 'Švicarska',
    'RS': 'Srbija',
    'BA': 'Bosna i Hercegovina',
    'ME': 'Crna Gora',
}


def _map_country(value: str | None) -> str | None:
    raw = (value or '').strip()
    if not raw:
        return None
    if len(raw) == 2 and raw.isalpha():
        return raw.upper()
    return _LEGACY_ALIASES.get(raw.casefold())


def backfill_country_code(apps, schema_editor):
    Partner = apps.get_model('partners', 'Partner')
    ambiguous = []
    for partner in Partner.objects.all().iterator():
        code = _map_country(partner.country)
        if code is None:
            ambiguous.append(partner.pk)
            continue
        partner.country_code = code
        partner.country = _DISPLAY.get(code, code)
        partner.save(update_fields=['country_code', 'country'])
    if ambiguous:
        logger.warning(
            'ADR-0023 ambiguous partner.country rows left null (no XX/auto-HR): %s',
            ambiguous,
        )


def normalize_tax_and_vat(apps, schema_editor):
    Partner = apps.get_model('partners', 'Partner')
    for partner in Partner.objects.all().iterator():
        tax = (partner.tax_number or '').replace('HR', '').replace('hr', '')
        tax = ''.join(ch for ch in tax if ch.isdigit())
        vat = ''.join((partner.vat_number or '').split()).upper()
        update = []
        if partner.tax_number != tax:
            partner.tax_number = tax
            update.append('tax_number')
        if partner.vat_number != vat:
            partner.vat_number = vat
            update.append('vat_number')
        if update:
            partner.save(update_fields=update)


class Migration(migrations.Migration):

    dependencies = [
        ('partners', '0006_partnerbankaccount_optional_bank_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='partner',
            name='country_code',
            field=models.CharField(max_length=2, null=True, verbose_name='Kod države'),
        ),
        migrations.AlterField(
            model_name='partner',
            name='tax_number',
            field=models.CharField(blank=True, max_length=20, verbose_name='Porezni broj'),
        ),
        migrations.RunPython(backfill_country_code, migrations.RunPython.noop),
        migrations.RunPython(normalize_tax_and_vat, migrations.RunPython.noop),
    ]
