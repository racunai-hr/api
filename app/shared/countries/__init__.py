"""Country code helpers — stub for ISO 3166 lookups."""

EU_COUNTRY_CODES = frozenset({
    'AT', 'BE', 'BG', 'CY', 'CZ', 'DE', 'DK', 'EE', 'ES', 'FI',
    'FR', 'GR', 'HR', 'HU', 'IE', 'IT', 'LT', 'LU', 'LV', 'MT',
    'NL', 'PL', 'PT', 'RO', 'SE', 'SI', 'SK',
})


def is_eu_country(code: str) -> bool:
    return (code or '').upper() in EU_COUNTRY_CODES


__all__ = ['EU_COUNTRY_CODES', 'is_eu_country']
