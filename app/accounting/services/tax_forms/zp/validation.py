"""XSD and business validation for Obrazac ZP."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from lxml import etree

from accounting.services.tax_forms.zp.payload import ZpPayload

SCHEMA_DIR = Path(__file__).resolve().parents[3] / 'schemas' / 'zp' / 'v1-0'
ZP_OBRAZAC_XSD = SCHEMA_DIR / 'ObrazacZP-v1-0.xsd'

_SCHEMA: etree.XMLSchema | None = None
_SIGNATURE_NS = 'http://www.w3.org/2000/09/xmldsig#'
_OIB_RE = re.compile(r'^\d{11}$')
_TAX_OFFICE_RE = re.compile(r'^\d{4}$')

_EU_COUNTRY_CODES = frozenset(
    {
        'AT', 'BE', 'BG', 'CY', 'CZ', 'DE', 'DK', 'EE', 'EL', 'ES', 'FI', 'FR', 'GB',
        'HU', 'IE', 'IT', 'LT', 'LU', 'LV', 'MT', 'NL', 'PL', 'PT', 'RO', 'SE', 'SI',
        'SK', 'XI',
    }
)


class ZpSchemaValidationError(Exception):
    """Raised when ZP XML fails XSD validation."""


class ZpValidationError(Exception):
    """Raised when ZpPayload fails business validation."""


def _get_schema() -> etree.XMLSchema:
    global _SCHEMA
    if _SCHEMA is not None:
        return _SCHEMA
    if not ZP_OBRAZAC_XSD.is_file():
        raise ZpSchemaValidationError(f'XSD nije pronađen: {ZP_OBRAZAC_XSD}')
    schema_doc = etree.parse(str(ZP_OBRAZAC_XSD))
    _SCHEMA = etree.XMLSchema(schema_doc)
    return _SCHEMA


def _strip_signatures(document: etree._Element) -> etree._Element:
    for signature in document.findall(f'{{{_SIGNATURE_NS}}}Signature'):
        document.remove(signature)
    return document


def validate_zp_xml(data: bytes | etree._Element, *, signed: bool = False) -> None:
    """Validate Obrazac ZP XML against local v1.0 XSD."""
    document = etree.fromstring(data) if isinstance(data, (bytes, bytearray)) else data
    if signed:
        signatures = document.findall(f'{{{_SIGNATURE_NS}}}Signature')
        if not signatures:
            raise ZpSchemaValidationError('Potpisani XML mora sadržavati XML Signature element.')
        document = _strip_signatures(etree.fromstring(etree.tostring(document)))
    schema = _get_schema()
    if not schema.validate(document):
        message = str(schema.error_log.last_error)
        raise ZpSchemaValidationError(message)


def validate_zp_payload(payload: ZpPayload) -> None:
    """Validate business rules not covered by XSD."""
    if not _OIB_RE.match(payload.taxpayer.oib or ''):
        raise ZpValidationError(f'Neispravan OIB obveznika: {payload.taxpayer.oib!r}')

    if payload.period_from > payload.period_to:
        raise ZpValidationError('Početni datum razdoblja mora biti prije završnog datuma.')

    if payload.tax_office_code and not _TAX_OFFICE_RE.match(payload.tax_office_code):
        raise ZpValidationError(f'Neispravna šifra ispostave: {payload.tax_office_code!r}')

    if len(payload.prepared_by.first_name.strip()) < 2 or len(payload.prepared_by.last_name.strip()) < 2:
        raise ZpValidationError('Ime i prezime sastavljača moraju imati najmanje 2 znaka.')

    seen: set[tuple[str, str]] = set()
    goods_total = Decimal('0.00')
    services_total = Decimal('0.00')

    for index, row in enumerate(payload.rows, start=1):
        if row.country_code not in _EU_COUNTRY_CODES:
            raise ZpValidationError(f'Red {index}: neispravan EU kod države {row.country_code!r}')

        pdv_id = (row.pdv_id or '').strip()
        if not pdv_id or len(pdv_id) > 12:
            raise ZpValidationError(f'Red {index}: Neispravan EU PDV ID: {row.pdv_id!r}')

        key = (row.country_code, pdv_id)
        if key in seen:
            raise ZpValidationError(f'Red {index}: duplicirani primatelj {row.country_code}/{pdv_id}')
        seen.add(key)

        goods_total += row.goods_value
        services_total += row.services_value

    if goods_total != payload.total_goods or services_total != payload.total_services:
        raise ZpValidationError('Zbrojevi redova ne odgovaraju ukupnim vrijednostima payloada.')
