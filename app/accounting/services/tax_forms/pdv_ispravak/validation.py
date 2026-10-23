"""XSD and ePorezna business rules for ObrazacPDVispravak."""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from lxml import etree

from accounting.services.tax_forms.pdv_ispravak.payload import PdvIspravakPayload, PdvIspravakRow

SCHEMA_DIR = Path(__file__).resolve().parents[3] / 'schemas' / 'pdv_ispravak' / 'v1-0'
PDV_ISPRAVAK_XSD = SCHEMA_DIR / 'ObrazacPDVispravak-v1-0.xsd'
_OIB_RE = re.compile(r'^\d{11}$')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_TWO = Decimal('0.01')
_RATES = (
    (Decimal('5'), 'base_5', 'vat_5', '5 %'),
    (Decimal('13'), 'base_13', 'vat_13', '13 %'),
    (Decimal('25'), 'base_25', 'vat_25', '25 %'),
)

_SCHEMA: etree.XMLSchema | None = None


class PdvIspravakSchemaError(Exception):
    """Raised when PDV-ispravak XML fails the official XSD."""


class PdvIspravakValidationError(Exception):
    """Raised when a PDV-ispravak payload breaks an ePorezna rule."""


def _get_schema() -> etree.XMLSchema:
    global _SCHEMA
    if _SCHEMA is not None:
        return _SCHEMA
    if not PDV_ISPRAVAK_XSD.is_file():
        raise PdvIspravakSchemaError(f'XSD nije pronađen: {PDV_ISPRAVAK_XSD}')
    _SCHEMA = etree.XMLSchema(etree.parse(str(PDV_ISPRAVAK_XSD)))
    return _SCHEMA


def validate_pdv_ispravak_xml(data: bytes | etree._Element) -> None:
    document = etree.fromstring(data) if isinstance(data, (bytes, bytearray)) else data
    schema = _get_schema()
    if not schema.validate(document):
        raise PdvIspravakSchemaError(str(schema.error_log.last_error))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_TWO, rounding=ROUND_HALF_UP)


def _expected_vat(base: Decimal, rate: Decimal) -> Decimal:
    return _money(base * rate / Decimal('100'))


def _shift_year(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _hr_date(value: date) -> str:
    return f'{value.day:02d}.{value.month:02d}.{value.year}.'


def invoice_date_window(period_to: date) -> tuple[date, date]:
    """Invoice dates allowed on a PDV-ispravak for this period end.

    The newest invoice is the last day of the same month one year earlier.
    The oldest invoice is 1 January of that previous year.
    """
    previous = _shift_year(period_to, -1)
    last_day = calendar.monthrange(previous.year, previous.month)[1]
    latest = previous.replace(day=last_day)
    earliest = date(previous.year, 1, 1)
    return earliest, latest


def validate_pdv_ispravak_payload(payload: PdvIspravakPayload, *, allow_empty: bool = False) -> None:
    taxpayer = payload.taxpayer
    if not _OIB_RE.match(taxpayer.oib or ''):
        raise PdvIspravakValidationError('OIB obveznika mora imati 11 znamenki.')
    if len((taxpayer.name or '').strip()) < 2:
        raise PdvIspravakValidationError('Naziv obveznika je obavezan.')
    if len((taxpayer.phone or '').strip()) < 9:
        raise PdvIspravakValidationError('Telefon obveznika mora imati najmanje 9 znakova.')
    if not _EMAIL_RE.match((taxpayer.email or '').strip()):
        raise PdvIspravakValidationError('Email obveznika nije ispravan.')
    if payload.period_from > payload.period_to:
        raise PdvIspravakValidationError('Početak razdoblja mora biti prije kraja.')
    preparer = payload.preparer
    if len(preparer.first_name.strip()) < 2 or len(preparer.last_name.strip()) < 2:
        raise PdvIspravakValidationError('Ime i prezime sastavljača moraju imati najmanje 2 znaka.')
    if len(preparer.place.strip()) < 2:
        raise PdvIspravakValidationError('Mjesto sastavljača je obavezno.')

    if not payload.rows and not allow_empty:
        raise PdvIspravakValidationError(
            'Prvi PDV-ispravak razdoblja mora imati barem jedan račun.'
        )

    earliest, latest = invoice_date_window(payload.period_to)
    for index, row in enumerate(payload.rows, start=1):
        _validate_row(row, index=index, earliest=earliest, latest=latest)


def _validate_row(row: PdvIspravakRow, *, index: int, earliest: date, latest: date) -> None:
    if not (row.invoice_number or '').strip():
        raise PdvIspravakValidationError(f'Red {index}: broj računa je obavezan.')
    if row.invoice_date < earliest or row.invoice_date > latest:
        raise PdvIspravakValidationError(
            f'Red {index}: datum računa mora biti između {_hr_date(earliest)} i {_hr_date(latest)}'
        )
    if row.identifier_kind not in {1, 2, 3}:
        raise PdvIspravakValidationError(f'Red {index}: oznaka poreznog broja smije biti 1, 2 ili 3.')
    identifier = (row.identifier or '').strip()
    if row.identifier_kind == 1 and not _OIB_RE.match(identifier):
        raise PdvIspravakValidationError(f'Red {index}: za oznaku 1 treba biti upisan OIB.')
    if row.identifier_kind == 2 and not re.fullmatch(r'[A-Za-z]{2}[A-Za-z0-9]{2,28}', identifier):
        raise PdvIspravakValidationError(f'Red {index}: za oznaku 2 treba biti upisan PDV ID.')
    if row.identifier_kind == 3 and not identifier:
        raise PdvIspravakValidationError(f'Red {index}: za oznaku 3 treba biti upisan porezni broj.')
    if len((row.customer_name or '').strip()) < 2:
        raise PdvIspravakValidationError(f'Red {index}: naziv kupca je obavezan.')

    positive = False
    for rate, base_name, vat_name, label in _RATES:
        base = getattr(row, base_name)
        vat = getattr(row, vat_name)
        if base < 0 or vat < 0:
            raise PdvIspravakValidationError(f'Red {index}: iznosi {label} ne mogu biti negativni.')
        if vat != _expected_vat(base, rate):
            raise PdvIspravakValidationError(
                f'Red {index}: ispravljeni PDV {label} mora biti osnovica × stopa.'
            )
        if base > 0 or vat > 0:
            positive = True
    if not positive:
        raise PdvIspravakValidationError(
            f'Red {index}: stavka mora imati iznos veći od 0,00 EUR po stopi 5, 13 ili 25 %.'
        )
