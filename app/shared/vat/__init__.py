"""VAT ID normalization helpers for Partner MDM (ADR-0023).

PDV rate helpers remain re-exported from Tax mapping until migrated.
"""

from __future__ import annotations

import re

from accounting.services.tax_forms.pdv.mapping import (
    invoice_rate_to_box,
    normalize_vat_rate,
)
from shared.countries import (
    COUNTRY_TO_VAT_PREFIX,
    EU_COUNTRY_CODES,
    VAT_PREFIX_TO_COUNTRY,
    normalize_country_code,
)

_SPACE_RE = re.compile(r'\s+')
_VAT_BODY_RE = re.compile(r'^[A-Z0-9]+$')


def normalize_vat_number(value: str | None) -> str:
    """Uppercase VAT ID without whitespace."""
    return _SPACE_RE.sub('', (value or '').strip()).upper()


def vat_prefix(vat_number: str) -> str:
    vat = normalize_vat_number(vat_number)
    return vat[:2] if len(vat) >= 2 else ''


def looks_like_eu_vat(vat_number: str) -> bool:
    """True when value starts with a known EU/VIES VAT prefix."""
    prefix = vat_prefix(vat_number)
    return prefix in VAT_PREFIX_TO_COUNTRY


def eu_vat_format_valid(vat_number: str) -> bool:
    """Lightweight EU VAT shape check (prefix + alphanumeric body, length bounds)."""
    vat = normalize_vat_number(vat_number)
    if len(vat) < 4 or len(vat) > 14:
        return False
    prefix = vat[:2]
    if prefix not in VAT_PREFIX_TO_COUNTRY:
        return False
    body = vat[2:]
    return bool(body) and bool(_VAT_BODY_RE.match(body))


def vat_country_code(vat_number: str) -> str | None:
    """ISO country inferred from VAT prefix, or None."""
    return VAT_PREFIX_TO_COUNTRY.get(vat_prefix(vat_number))


def vat_matches_country(vat_number: str, country_code: str) -> bool:
    """Whether VAT prefix corresponds to the partner country_code."""
    code = normalize_country_code(country_code)
    expected = COUNTRY_TO_VAT_PREFIX.get(code)
    if not expected:
        return False
    return vat_prefix(vat_number) == expected


def is_eu_vat_country(country_code: str) -> bool:
    return normalize_country_code(country_code) in EU_COUNTRY_CODES


__all__ = [
    'eu_vat_format_valid',
    'invoice_rate_to_box',
    'is_eu_vat_country',
    'looks_like_eu_vat',
    'normalize_vat_number',
    'normalize_vat_rate',
    'vat_country_code',
    'vat_matches_country',
    'vat_prefix',
]
