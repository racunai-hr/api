"""Parse Obrazac TZ2 v1.0 XML into Tz2Payload."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from accounting.services.tax_forms.tz2.payload import (
    TZ2_MAPPING_VERSION,
    TZ2_SCHEMA_VERSION,
    Tz2Payload,
    Tz2PreparedBy,
    Tz2Taxpayer,
    money,
)

_OBRAZAC_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacTZ2/v1-0'
_METAPODACI_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'


def _local(tag: str) -> str:
    return f'{{{_OBRAZAC_NS}}}{tag}'


def _parse_decimal(text: str | None) -> Decimal:
    if not text:
        return money('0.00')
    try:
        return money(text.strip().replace(',', '.'))
    except InvalidOperation as exc:
        raise ValueError(f'Invalid decimal in TZ2 XML: {text!r}') from exc


def _parse_int(text: str | None) -> int:
    if not text:
        return 0
    return int(text.strip())


def _find_text(element: ET.Element | None, tag: str, ns: str = _OBRAZAC_NS) -> str:
    if element is None:
        return ''
    child = element.find(f'{{{ns}}}{tag}')
    return (child.text or '').strip() if child is not None else ''


@dataclass(frozen=True)
class Tz2XmlMeta:
    identifikator: UUID
    datum: datetime
    autor: str


def parse_tz2_xml_meta(data: bytes) -> Tz2XmlMeta:
    """Extract Metapodaci Identifikator / Datum from Obrazac TZ 2 XML."""
    root = ET.fromstring(data)
    meta = root.find(f'{{{_METAPODACI_NS}}}Metapodaci')
    ident_text = _find_text(meta, 'Identifikator', ns=_METAPODACI_NS)
    datum_text = _find_text(meta, 'Datum', ns=_METAPODACI_NS)
    autor = _find_text(meta, 'Autor', ns=_METAPODACI_NS)
    if not ident_text:
        raise ValueError('Obrazac TZ2 XML missing Metapodaci Identifikator')
    if not datum_text:
        raise ValueError('Obrazac TZ2 XML missing Metapodaci Datum')
    try:
        identifikator = UUID(ident_text)
    except ValueError as exc:
        raise ValueError(f'Invalid TZ2 Identifikator: {ident_text!r}') from exc
    try:
        datum = datetime.fromisoformat(datum_text)
    except ValueError as exc:
        raise ValueError(f'Invalid TZ2 Datum: {datum_text!r}') from exc
    return Tz2XmlMeta(identifikator=identifikator, datum=datum, autor=autor)


def parse_tz2_xml(data: bytes) -> Tz2Payload:
    """Parse Obrazac TZ 2 XML into canonical Tz2Payload."""
    root = ET.fromstring(data)
    header = root.find(_local('Zaglavlje'))
    body = root.find(_local('Tijelo'))
    if header is None:
        raise ValueError('Obrazac TZ2 XML missing Zaglavlje element')
    if body is None:
        raise ValueError('Obrazac TZ2 XML missing Tijelo element')

    period = header.find(_local('Razdoblje'))
    if period is None:
        raise ValueError('Obrazac TZ2 XML missing Razdoblje element')
    period_from = date.fromisoformat(_find_text(period, 'DatumOd'))
    period_to = date.fromisoformat(_find_text(period, 'DatumDo'))

    obveznik = header.find(_local('Obveznik'))
    if obveznik is None:
        raise ValueError('Obrazac TZ2 XML missing Obveznik element')
    adresa = obveznik.find(_local('Adresa'))

    meta = root.find(f'{{{_METAPODACI_NS}}}Metapodaci')
    author = _find_text(meta, 'Autor', ns=_METAPODACI_NS)
    author_parts = author.split(None, 1)
    prepared_first = author_parts[0] if author_parts else 'NA'
    prepared_last = author_parts[1] if len(author_parts) > 1 else 'NA'

    schema_version = root.attrib.get('verzijaSheme', TZ2_SCHEMA_VERSION)
    payment_installments = (_find_text(body, 'Podatak34') == '1')

    return Tz2Payload(
        schema_version=schema_version,
        mapping_version=TZ2_MAPPING_VERSION,
        period_from=period_from,
        period_to=period_to,
        taxpayer=Tz2Taxpayer(
            first_name=_find_text(obveznik, 'Ime'),
            last_name=_find_text(obveznik, 'Prezime'),
            oib=_find_text(obveznik, 'OIB'),
            municipality_code=_find_text(adresa, 'SifraOpcine'),
            city=_find_text(adresa, 'Mjesto'),
            street=_find_text(adresa, 'Ulica'),
            house_number=_find_text(adresa, 'Broj'),
        ),
        prepared_by=Tz2PreparedBy(first_name=prepared_first, last_name=prepared_last),
        room_beds=_parse_int(body.findtext(_local('Podatak01'))),
        room_bed_rate=_parse_decimal(body.findtext(_local('Podatak02'))),
        aux_beds=_parse_int(body.findtext(_local('Podatak04'))),
        aux_bed_rate=_parse_decimal(body.findtext(_local('Podatak05'))),
        camp_units=_parse_int(body.findtext(_local('Podatak07'))),
        camp_rate=_parse_decimal(body.findtext(_local('Podatak08'))),
        robinson_units=_parse_int(body.findtext(_local('Podatak10'))),
        robinson_rate=_parse_decimal(body.findtext(_local('Podatak11'))),
        opg_room_beds=_parse_int(body.findtext(_local('Podatak13'))),
        opg_room_bed_rate=_parse_decimal(body.findtext(_local('Podatak14'))),
        opg_aux_beds=_parse_int(body.findtext(_local('Podatak16'))),
        opg_aux_bed_rate=_parse_decimal(body.findtext(_local('Podatak17'))),
        opg_camp_units=_parse_int(body.findtext(_local('Podatak19'))),
        opg_camp_rate=_parse_decimal(body.findtext(_local('Podatak20'))),
        opg_robinson_units=_parse_int(body.findtext(_local('Podatak22'))),
        opg_robinson_rate=_parse_decimal(body.findtext(_local('Podatak23'))),
        discount_group_1=_parse_decimal(body.findtext(_local('Podatak26'))),
        discount_group_2=_parse_decimal(body.findtext(_local('Podatak27'))),
        discount_group_3=_parse_decimal(body.findtext(_local('Podatak28'))),
        discount_group_4=_parse_decimal(body.findtext(_local('Podatak29'))),
        payment_installments=payment_installments,
        ep_receipts=_parse_decimal(body.findtext(_local('Podatak36'))),
    )
