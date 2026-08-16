"""Render unsigned Obrazac PDV-S v1.0 XML."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from uuid import uuid4

from accounting.services.tax_forms.pdv_s.payload import PDV_S_SCHEMA_VERSION, PdvSPayload

_OBRAZAC_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacPDVS/v1-0'
_METAPODACI_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'
_DC_TITLE = 'http://purl.org/dc/elements/1.1/title'
_DC_CREATOR = 'http://purl.org/dc/elements/1.1/creator'
_DC_DATE = 'http://purl.org/dc/elements/1.1/date'
_DC_FORMAT = 'http://purl.org/dc/elements/1.1/format'
_DC_LANGUAGE = 'http://purl.org/dc/elements/1.1/language'
_DC_IDENTIFIER = 'http://purl.org/dc/elements/1.1/identifier'
_DC_CONFORMS_TO = 'http://purl.org/dc/terms/conformsTo'
_DC_TYPE = 'http://purl.org/dc/elements/1.1/type'


def _local(tag: str) -> str:
    return f'{{{_OBRAZAC_NS}}}{tag}'


def _meta(tag: str) -> str:
    return f'{{{_METAPODACI_NS}}}{tag}'


def _format_decimal(value) -> str:
    return f'{value:.2f}'


def _append_metapodaci(root: ET.Element, payload: PdvSPayload) -> None:
    author = f'{payload.prepared_by.first_name} {payload.prepared_by.last_name}'.strip()
    metapodaci = ET.SubElement(root, _meta('Metapodaci'))

    naslov = ET.SubElement(metapodaci, _meta('Naslov'))
    naslov.set('dc', _DC_TITLE)
    naslov.text = 'Obrazac PDV-S'

    autor = ET.SubElement(metapodaci, _meta('Autor'))
    autor.set('dc', _DC_CREATOR)
    autor.text = author.upper()

    datum = ET.SubElement(metapodaci, _meta('Datum'))
    datum.set('dc', _DC_DATE)
    datum.text = datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()

    format_elem = ET.SubElement(metapodaci, _meta('Format'))
    format_elem.set('dc', _DC_FORMAT)
    format_elem.text = 'text/xml'

    jezik = ET.SubElement(metapodaci, _meta('Jezik'))
    jezik.set('dc', _DC_LANGUAGE)
    jezik.text = 'hr-HR'

    identifikator = ET.SubElement(metapodaci, _meta('Identifikator'))
    identifikator.set('dc', _DC_IDENTIFIER)
    identifikator.text = str(uuid4())

    uskladjenost = ET.SubElement(metapodaci, _meta('Uskladjenost'))
    uskladjenost.set('dc', _DC_CONFORMS_TO)
    uskladjenost.text = 'ObrazacPDVS-v1-0'

    tip = ET.SubElement(metapodaci, _meta('Tip'))
    tip.set('dc', _DC_TYPE)
    tip.text = 'Elektronički obrazac'

    adresant = ET.SubElement(metapodaci, _meta('Adresant'))
    adresant.text = 'Ministarstvo Financija, Porezna uprava, Zagreb'


def render_pdv_s_xml(payload: PdvSPayload) -> bytes:
    root = ET.Element(
        _local('ObrazacPDVS'),
        {'verzijaSheme': PDV_S_SCHEMA_VERSION},
    )
    root.set('xmlns', _OBRAZAC_NS)

    _append_metapodaci(root, payload)

    zaglavlje = ET.SubElement(root, _local('Zaglavlje'))
    razdoblje = ET.SubElement(zaglavlje, _local('Razdoblje'))
    ET.SubElement(razdoblje, _local('DatumOd')).text = payload.period_from.isoformat()
    ET.SubElement(razdoblje, _local('DatumDo')).text = payload.period_to.isoformat()

    obveznik = ET.SubElement(zaglavlje, _local('Obveznik'))
    ET.SubElement(obveznik, _local('Naziv')).text = payload.taxpayer.name
    ET.SubElement(obveznik, _local('OIB')).text = payload.taxpayer.oib
    adresa = ET.SubElement(obveznik, _local('Adresa'))
    ET.SubElement(adresa, _local('Mjesto')).text = payload.taxpayer.city
    ET.SubElement(adresa, _local('Ulica')).text = payload.taxpayer.street
    ET.SubElement(adresa, _local('Broj')).text = payload.taxpayer.house_number

    obracun = ET.SubElement(zaglavlje, _local('ObracunSastavio'))
    ET.SubElement(obracun, _local('Ime')).text = payload.prepared_by.first_name
    ET.SubElement(obracun, _local('Prezime')).text = payload.prepared_by.last_name
    if payload.prepared_by.email:
        ET.SubElement(obracun, _local('Email')).text = payload.prepared_by.email
    if payload.prepared_by.phone:
        ET.SubElement(obracun, _local('Telefon')).text = payload.prepared_by.phone
    ET.SubElement(zaglavlje, _local('Ispostava')).text = payload.tax_office_code

    tijelo = ET.SubElement(root, _local('Tijelo'))
    isporuke = ET.SubElement(tijelo, _local('Isporuke'))
    for index, row in enumerate(payload.rows, start=1):
        isporuka = ET.SubElement(isporuke, _local('Isporuka'))
        ET.SubElement(isporuka, _local('RedBr')).text = str(index)
        ET.SubElement(isporuka, _local('KodDrzave')).text = row.country_code
        ET.SubElement(isporuka, _local('PDVID')).text = row.pdv_id
        ET.SubElement(isporuka, _local('I1')).text = _format_decimal(row.goods_value)
        ET.SubElement(isporuka, _local('I2')).text = _format_decimal(row.services_value)

    ukupno = ET.SubElement(tijelo, _local('IsporukeUkupno'))
    ET.SubElement(ukupno, _local('I1')).text = _format_decimal(payload.total_goods)
    ET.SubElement(ukupno, _local('I2')).text = _format_decimal(payload.total_services)

    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
