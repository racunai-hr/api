"""Parse Obrazac ZP v1.0 XML (unsigned or signed) into ZpPayload."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

from accounting.services.tax_forms.zp.payload import (
    ZP_MAPPING_VERSION,
    ZP_SCHEMA_VERSION,
    ZpPayload,
    ZpPreparedBy,
    ZpRow,
    ZpTaxpayer,
)

_OBRAZAC_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacZP/v1-0'


def _local(tag: str) -> str:
    return f'{{{_OBRAZAC_NS}}}{tag}'


def _parse_decimal(text: str | None) -> Decimal:
    if not text:
        return Decimal('0.00')
    try:
        return Decimal(text.strip().replace(',', '.')).quantize(Decimal('0.01'))
    except InvalidOperation as exc:
        raise ValueError(f'Invalid decimal in ZP XML: {text!r}') from exc


def _parse_date(text: str | None) -> date:
    if not text:
        raise ValueError('ZP XML missing date value')
    return date.fromisoformat(text.strip())


def _find_text(element: ET.Element | None, tag: str) -> str:
    if element is None:
        return ''
    child = element.find(_local(tag))
    return (child.text or '').strip() if child is not None else ''


def parse_zp_xml(data: bytes) -> ZpPayload:
    """Parse Obrazac ZP XML into canonical ZpPayload."""
    root = ET.fromstring(data)
    header = root.find(_local('Zaglavlje'))
    body = root.find(_local('Tijelo'))
    if header is None:
        raise ValueError('Obrazac ZP XML missing Zaglavlje element')
    if body is None:
        raise ValueError('Obrazac ZP XML missing Tijelo element')

    period = header.find(_local('Razdoblje'))
    if period is None:
        raise ValueError('Obrazac ZP XML missing Razdoblje element')
    period_from = _parse_date(period.findtext(_local('DatumOd')))
    period_to = _parse_date(period.findtext(_local('DatumDo')))

    obveznik = header.find(_local('Obveznik'))
    if obveznik is None:
        raise ValueError('Obrazac ZP XML missing Obveznik element')
    adresa = obveznik.find(_local('Adresa'))

    obracun = header.find(_local('ObracunSastavio'))
    prepared_by = ZpPreparedBy(
        first_name=_find_text(obracun, 'Ime') or 'NA',
        last_name=_find_text(obracun, 'Prezime') or 'NA',
        email=_find_text(obracun, 'Email'),
        phone=_find_text(obracun, 'Telefon'),
    )

    rows: list[ZpRow] = []
    isporuke = body.find(_local('Isporuke'))
    if isporuke is not None:
        for row in isporuke.findall(_local('Isporuka')):
            rows.append(
                ZpRow(
                    country_code=_find_text(row, 'KodDrzave'),
                    pdv_id=_find_text(row, 'PDVID'),
                    goods_value=_parse_decimal(row.findtext(_local('I1'))),
                    services_value=_parse_decimal(row.findtext(_local('I4'))),
                )
            )

    schema_version = root.attrib.get('verzijaSheme', ZP_SCHEMA_VERSION)

    return ZpPayload(
        schema_version=schema_version,
        mapping_version=ZP_MAPPING_VERSION,
        period_from=period_from,
        period_to=period_to,
        taxpayer=ZpTaxpayer(
            name=_find_text(obveznik, 'Naziv'),
            oib=_find_text(obveznik, 'OIB'),
            city=_find_text(adresa, 'Mjesto'),
            street=_find_text(adresa, 'Ulica'),
            house_number=_find_text(adresa, 'Broj'),
        ),
        prepared_by=prepared_by,
        tax_office_code=_find_text(header, 'Ispostava'),
        rows=tuple(rows),
    )
