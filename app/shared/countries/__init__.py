"""ISO 3166-1 country catalog and partner geographic jurisdiction (ADR-0023)."""

from __future__ import annotations

# Official ISO 3166-1 alpha-2 (UN M.49 / common registry subset used as MDM catalog).
ISO_3166_1_ALPHA2 = frozenset({
    'AD', 'AE', 'AF', 'AG', 'AI', 'AL', 'AM', 'AO', 'AQ', 'AR', 'AS', 'AT', 'AU', 'AW', 'AX', 'AZ',
    'BA', 'BB', 'BD', 'BE', 'BF', 'BG', 'BH', 'BI', 'BJ', 'BL', 'BM', 'BN', 'BO', 'BQ', 'BR', 'BS',
    'BT', 'BV', 'BW', 'BY', 'BZ', 'CA', 'CC', 'CD', 'CF', 'CG', 'CH', 'CI', 'CK', 'CL', 'CM', 'CN',
    'CO', 'CR', 'CU', 'CV', 'CW', 'CX', 'CY', 'CZ', 'DE', 'DJ', 'DK', 'DM', 'DO', 'DZ', 'EC', 'EE',
    'EG', 'EH', 'ER', 'ES', 'ET', 'FI', 'FJ', 'FK', 'FM', 'FO', 'FR', 'GA', 'GB', 'GD', 'GE', 'GF',
    'GG', 'GH', 'GI', 'GL', 'GM', 'GN', 'GP', 'GQ', 'GR', 'GS', 'GT', 'GU', 'GW', 'GY', 'HK', 'HM',
    'HN', 'HR', 'HT', 'HU', 'ID', 'IE', 'IL', 'IM', 'IN', 'IO', 'IQ', 'IR', 'IS', 'IT', 'JE', 'JM',
    'JO', 'JP', 'KE', 'KG', 'KH', 'KI', 'KM', 'KN', 'KP', 'KR', 'KW', 'KY', 'KZ', 'LA', 'LB', 'LC',
    'LI', 'LK', 'LR', 'LS', 'LT', 'LU', 'LV', 'LY', 'MA', 'MC', 'MD', 'ME', 'MF', 'MG', 'MH', 'MK',
    'ML', 'MM', 'MN', 'MO', 'MP', 'MQ', 'MR', 'MS', 'MT', 'MU', 'MV', 'MW', 'MX', 'MY', 'MZ', 'NA',
    'NC', 'NE', 'NF', 'NG', 'NI', 'NL', 'NO', 'NP', 'NR', 'NU', 'NZ', 'OM', 'PA', 'PE', 'PF', 'PG',
    'PH', 'PK', 'PL', 'PM', 'PN', 'PR', 'PS', 'PT', 'PW', 'PY', 'QA', 'RE', 'RO', 'RS', 'RU', 'RW',
    'SA', 'SB', 'SC', 'SD', 'SE', 'SG', 'SH', 'SI', 'SJ', 'SK', 'SL', 'SM', 'SN', 'SO', 'SR', 'SS',
    'ST', 'SV', 'SX', 'SY', 'SZ', 'TC', 'TD', 'TF', 'TG', 'TH', 'TJ', 'TK', 'TL', 'TM', 'TN', 'TO',
    'TR', 'TT', 'TV', 'TW', 'TZ', 'UA', 'UG', 'UM', 'US', 'UY', 'UZ', 'VA', 'VC', 'VE', 'VG', 'VI',
    'VN', 'VU', 'WF', 'WS', 'YE', 'YT', 'ZA', 'ZM', 'ZW',
})

# EU member states including HR (geographic membership).
EU_COUNTRY_CODES = frozenset({
    'AT', 'BE', 'BG', 'CY', 'CZ', 'DE', 'DK', 'EE', 'ES', 'FI',
    'FR', 'GR', 'HR', 'HU', 'IE', 'IT', 'LT', 'LU', 'LV', 'MT',
    'NL', 'PL', 'PT', 'RO', 'SE', 'SI', 'SK',
})

# Display names (HR) for UI / legacy `country` sync. Fallback = ISO code.
COUNTRY_DISPLAY_HR: dict[str, str] = {
    'AT': 'Austrija',
    'BE': 'Belgija',
    'BG': 'Bugarska',
    'CY': 'Cipar',
    'CZ': 'Češka',
    'DE': 'Njemačka',
    'DK': 'Danska',
    'EE': 'Estonija',
    'ES': 'Španjolska',
    'FI': 'Finska',
    'FR': 'Francuska',
    'GR': 'Grčka',
    'HR': 'Hrvatska',
    'HU': 'Mađarska',
    'IE': 'Irska',
    'IT': 'Italija',
    'LT': 'Litva',
    'LU': 'Luksemburg',
    'LV': 'Latvija',
    'MT': 'Malta',
    'NL': 'Nizozemska',
    'PL': 'Poljska',
    'PT': 'Portugal',
    'RO': 'Rumunjska',
    'SE': 'Švedska',
    'SI': 'Slovenija',
    'SK': 'Slovačka',
    'GB': 'Ujedinjeno Kraljevstvo',
    'US': 'Sjedinjene Američke Države',
    'CH': 'Švicarska',
    'NO': 'Norveška',
    'RS': 'Srbija',
    'BA': 'Bosna i Hercegovina',
    'ME': 'Crna Gora',
    'MK': 'Sjeverna Makedonija',
    'AL': 'Albanija',
    'TR': 'Turska',
    'UA': 'Ukrajina',
    'CN': 'Kina',
    'JP': 'Japan',
    'AU': 'Australija',
    'CA': 'Kanada',
}

# Deterministic legacy `country` string → ISO2 (lowercase keys).
LEGACY_COUNTRY_ALIASES: dict[str, str] = {
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
    'magyarország': 'HU',
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
    'holland': 'NL',
    'pl': 'PL',
    'poland': 'PL',
    'poljska': 'PL',
    'cz': 'CZ',
    'czech': 'CZ',
    'czechia': 'CZ',
    'czech republic': 'CZ',
    'češka': 'CZ',
    'ceska': 'CZ',
    'sk': 'SK',
    'slovakia': 'SK',
    'slovačka': 'SK',
    'slovacka': 'SK',
    'es': 'ES',
    'spain': 'ES',
    'españa': 'ES',
    'spanjolska': 'ES',
    'pt': 'PT',
    'portugal': 'PT',
    'gr': 'GR',
    'greece': 'GR',
    'ελλάδα': 'GR',
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
    'great britain': 'GB',
    'ujedinjeno kraljevstvo': 'GB',
    'us': 'US',
    'usa': 'US',
    'united states': 'US',
    'united states of america': 'US',
    'sjedinjene američke države': 'US',
    'ch': 'CH',
    'switzerland': 'CH',
    'schweiz': 'CH',
    'švicarska': 'CH',
    'svicarska': 'CH',
    'rs': 'RS',
    'serbia': 'RS',
    'srbija': 'RS',
    'ba': 'BA',
    'bosnia': 'BA',
    'bosnia and herzegovina': 'BA',
    'bosna i hercegovina': 'BA',
    'me': 'ME',
    'montenegro': 'ME',
    'crna gora': 'ME',
}

JURISDICTION_HR = 'HR'
JURISDICTION_EU = 'EU'
JURISDICTION_NON_EU = 'NON_EU'
VALID_JURISDICTIONS = frozenset({JURISDICTION_HR, JURISDICTION_EU, JURISDICTION_NON_EU})

# VAT ID country prefix → ISO country_code (VIES uses EL for Greece).
VAT_PREFIX_TO_COUNTRY = {
    'AT': 'AT', 'BE': 'BE', 'BG': 'BG', 'CY': 'CY', 'CZ': 'CZ', 'DE': 'DE', 'DK': 'DK',
    'EE': 'EE', 'EL': 'GR', 'ES': 'ES', 'FI': 'FI', 'FR': 'FR', 'HR': 'HR', 'HU': 'HU',
    'IE': 'IE', 'IT': 'IT', 'LT': 'LT', 'LU': 'LU', 'LV': 'LV', 'MT': 'MT', 'NL': 'NL',
    'PL': 'PL', 'PT': 'PT', 'RO': 'RO', 'SE': 'SE', 'SI': 'SI', 'SK': 'SK', 'XI': 'GB',
}

COUNTRY_TO_VAT_PREFIX = {iso: prefix for prefix, iso in VAT_PREFIX_TO_COUNTRY.items()}
COUNTRY_TO_VAT_PREFIX['GR'] = 'EL'


def is_eu_country(code: str) -> bool:
    return (code or '').upper() in EU_COUNTRY_CODES


def is_valid_country_code(code: str) -> bool:
    return (code or '').upper() in ISO_3166_1_ALPHA2


def normalize_country_code(value: str | None) -> str:
    return (value or '').strip().upper()


def country_display_name(code: str) -> str:
    normalized = normalize_country_code(code)
    return COUNTRY_DISPLAY_HR.get(normalized, normalized)


def map_legacy_country(value: str | None) -> str | None:
    """Deterministic legacy country string → ISO2, or None if ambiguous."""
    raw = (value or '').strip()
    if not raw:
        return None
    if len(raw) == 2 and raw.isalpha():
        code = raw.upper()
        return code if code in ISO_3166_1_ALPHA2 else None
    key = raw.casefold()
    return LEGACY_COUNTRY_ALIASES.get(key)


def derive_jurisdiction(country_code: str | None) -> str:
    code = normalize_country_code(country_code)
    if code == 'HR':
        return JURISDICTION_HR
    if code in EU_COUNTRY_CODES:
        return JURISDICTION_EU
    return JURISDICTION_NON_EU


__all__ = [
    'COUNTRY_DISPLAY_HR',
    'COUNTRY_TO_VAT_PREFIX',
    'EU_COUNTRY_CODES',
    'ISO_3166_1_ALPHA2',
    'JURISDICTION_EU',
    'JURISDICTION_HR',
    'JURISDICTION_NON_EU',
    'LEGACY_COUNTRY_ALIASES',
    'VALID_JURISDICTIONS',
    'VAT_PREFIX_TO_COUNTRY',
    'country_display_name',
    'derive_jurisdiction',
    'is_eu_country',
    'is_valid_country_code',
    'map_legacy_country',
    'normalize_country_code',
]
