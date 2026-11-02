"""XSD and business validation for Obrazac TZ 2."""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from accounting.services.tax_forms.tz2.payload import Tz2Payload
from accounting.services.tax_forms.tz2.verify import verify_tz2_arithmetic

SCHEMA_DIR = Path(__file__).resolve().parents[3] / 'schemas' / 'tz2' / 'v1-0'
TZ2_OBRAZAC_XSD = SCHEMA_DIR / 'ObrazacTZ2-v1-0.xsd'

_SCHEMA: etree.XMLSchema | None = None
_SIGNATURE_NS = 'http://www.w3.org/2000/09/xmldsig#'
_OIB_RE = re.compile(r'^\d{11}$')
_MUNICIPALITY_RE = re.compile(r'^\d{3}$')


class Tz2SchemaValidationError(Exception):
    """Raised when TZ2 XML fails XSD validation."""


class Tz2ValidationError(Exception):
    """Raised when Tz2Payload fails business validation."""


def _get_schema() -> etree.XMLSchema:
    global _SCHEMA
    if _SCHEMA is not None:
        return _SCHEMA
    if not TZ2_OBRAZAC_XSD.is_file():
        raise Tz2SchemaValidationError(f'XSD nije pronađen: {TZ2_OBRAZAC_XSD}')
    schema_doc = etree.parse(str(TZ2_OBRAZAC_XSD))
    _SCHEMA = etree.XMLSchema(schema_doc)
    return _SCHEMA


def _strip_signatures(document: etree._Element) -> etree._Element:
    for signature in document.findall(f'{{{_SIGNATURE_NS}}}Signature'):
        document.remove(signature)
    return document


def validate_tz2_xml(data: bytes | etree._Element, *, signed: bool = False) -> None:
    """Validate Obrazac TZ 2 XML against local v1.0 XSD."""
    document = etree.fromstring(data) if isinstance(data, (bytes, bytearray)) else data
    if signed:
        signatures = document.findall(f'{{{_SIGNATURE_NS}}}Signature')
        if not signatures:
            raise Tz2SchemaValidationError('Potpisani XML mora sadržavati XML Signature element.')
        document = _strip_signatures(etree.fromstring(etree.tostring(document)))
    schema = _get_schema()
    if not schema.validate(document):
        message = str(schema.error_log.last_error)
        raise Tz2SchemaValidationError(message)


def validate_tz2_payload(payload: Tz2Payload) -> None:
    """Validate business rules not covered by XSD."""
    if not _OIB_RE.match(payload.taxpayer.oib or ''):
        raise Tz2ValidationError(f'Neispravan OIB obveznika: {payload.taxpayer.oib!r}')

    if len(payload.taxpayer.first_name.strip()) < 2 or len(payload.taxpayer.last_name.strip()) < 2:
        raise Tz2ValidationError('Ime i prezime obveznika moraju imati najmanje 2 znaka.')

    if ' ' in payload.taxpayer.first_name.strip() and not payload.taxpayer.last_name.strip():
        raise Tz2ValidationError('Prezime obveznika je obavezno — ne cijepati company_name u Ime.')

    if payload.period_from > payload.period_to:
        raise Tz2ValidationError('Početni datum razdoblja mora biti prije završnog datuma.')

    if payload.period_from.year != payload.period_to.year:
        raise Tz2ValidationError('TZ 2 razdoblje mora biti unutar jedne kalendarske godine.')

    if not _MUNICIPALITY_RE.match(payload.taxpayer.municipality_code or ''):
        raise Tz2ValidationError(f'Neispravna šifra općine: {payload.taxpayer.municipality_code!r}')

    if not payload.taxpayer.house_number:
        raise Tz2ValidationError('Kućni broj je obavezan.')

    if len(payload.prepared_by.first_name.strip()) < 2 or len(payload.prepared_by.last_name.strip()) < 2:
        raise Tz2ValidationError('Ime i prezime sastavljača moraju imati najmanje 2 znaka.')

    for name, count in (
        ('osnovni kreveti', payload.room_beds),
        ('pomoćni kreveti', payload.aux_beds),
        ('kamp', payload.camp_units),
        ('robinzon', payload.robinson_units),
        ('OPG kreveti', payload.opg_room_beds),
        ('OPG pomoćni', payload.opg_aux_beds),
        ('OPG kamp', payload.opg_camp_units),
        ('OPG robinzon', payload.opg_robinson_units),
    ):
        if count < 0:
            raise Tz2ValidationError(f'{name}: broj ne smije biti negativan.')

    errors = verify_tz2_arithmetic(payload)
    if errors:
        raise Tz2ValidationError('; '.join(errors))
