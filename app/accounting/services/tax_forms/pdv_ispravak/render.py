"""Render unsigned ObrazacPDVispravak v1.0 XML."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from accounting.services.tax_forms.pdv_ispravak.payload import SCHEMA_VERSION, PdvIspravakPayload

_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacPDVispravak/v1-0'
_META_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'
_ZAGREB = ZoneInfo('Europe/Zagreb')


def _tag(name: str) -> str:
    return f'{{{_NS}}}{name}'


def _meta(name: str) -> str:
    return f'{{{_META_NS}}}{name}'


def _money(value: Decimal) -> str:
    return f'{value:.2f}'


def _meta_date() -> str:
    return datetime.now(_ZAGREB).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')


def render_pdv_ispravak_xml(payload: PdvIspravakPayload) -> bytes:
    root = ET.Element(_tag('ObrazacPDVispravak'), {'verzijaSheme': SCHEMA_VERSION})
    root.set('xmlns', _NS)

    meta = ET.SubElement(root, _meta('Metapodaci'))
    author = f'{payload.preparer.first_name} {payload.preparer.last_name}'.strip()
    fields = (
        ('Naslov', 'http://purl.org/dc/elements/1.1/title', 'Obrazac PDV-ispravak'),
        ('Autor', 'http://purl.org/dc/elements/1.1/creator', author.upper()),
        ('Datum', 'http://purl.org/dc/elements/1.1/date', _meta_date()),
        ('Format', 'http://purl.org/dc/elements/1.1/format', 'text/xml'),
        ('Jezik', 'http://purl.org/dc/elements/1.1/language', 'hr-HR'),
        ('Identifikator', 'http://purl.org/dc/elements/1.1/identifier', str(uuid4())),
        ('Uskladjenost', 'http://purl.org/dc/terms/conformsTo', 'ObrazacPDVispravak-v1-0'),
        ('Tip', 'http://purl.org/dc/elements/1.1/type', 'Elektronički obrazac'),
    )
    for name, dc, text in fields:
        element = ET.SubElement(meta, _meta(name))
        element.set('dc', dc)
        element.text = text
    ET.SubElement(meta, _meta('Adresant')).text = 'Ministarstvo Financija, Porezna uprava, Zagreb'

    header = ET.SubElement(root, _tag('Zaglavlje'))
    taxpayer = ET.SubElement(header, _tag('Obveznik'))
    ET.SubElement(taxpayer, _tag('Naziv')).text = payload.taxpayer.name
    ET.SubElement(taxpayer, _tag('OIB')).text = payload.taxpayer.oib
    address = ET.SubElement(taxpayer, _tag('Adresa'))
    ET.SubElement(address, _tag('Mjesto')).text = payload.taxpayer.city
    ET.SubElement(address, _tag('Ulica')).text = payload.taxpayer.street
    ET.SubElement(address, _tag('Broj')).text = payload.taxpayer.house_number
    ET.SubElement(taxpayer, _tag('Telefon')).text = payload.taxpayer.phone
    ET.SubElement(taxpayer, _tag('Email')).text = payload.taxpayer.email

    preparer = ET.SubElement(header, _tag('ObracunSastavio'))
    ET.SubElement(preparer, _tag('Ime')).text = payload.preparer.first_name
    ET.SubElement(preparer, _tag('Prezime')).text = payload.preparer.last_name
    ET.SubElement(preparer, _tag('Mjesto')).text = payload.preparer.place
    ET.SubElement(preparer, _tag('Datum')).text = payload.preparer.prepared_on.isoformat()

    period = ET.SubElement(header, _tag('Razdoblje'))
    ET.SubElement(period, _tag('DatumOd')).text = payload.period_from.isoformat()
    ET.SubElement(period, _tag('DatumDo')).text = payload.period_to.isoformat()

    body = ET.SubElement(root, _tag('Tijelo'))
    invoices = ET.SubElement(body, _tag('Racuni'))
    for index, row in enumerate(payload.rows, start=1):
        item = ET.SubElement(invoices, _tag('Stavka'))
        ET.SubElement(item, _tag('RedniBroj')).text = str(index)
        ET.SubElement(item, _tag('BrojRacuna')).text = row.invoice_number
        ET.SubElement(item, _tag('DatumRacuna')).text = row.invoice_date.isoformat()
        ET.SubElement(item, _tag('OznakaIdentifikatora')).text = str(row.identifier_kind)
        ET.SubElement(item, _tag('Identifikator')).text = row.identifier
        ET.SubElement(item, _tag('NazivImePrezime')).text = row.customer_name
        ET.SubElement(item, _tag('Isporuka5Osnovica')).text = _money(row.base_5)
        ET.SubElement(item, _tag('Isporuka5Ispravak')).text = _money(row.vat_5)
        ET.SubElement(item, _tag('Isporuka13Osnovica')).text = _money(row.base_13)
        ET.SubElement(item, _tag('Isporuka13Ispravak')).text = _money(row.vat_13)
        ET.SubElement(item, _tag('Isporuka25Osnovica')).text = _money(row.base_25)
        ET.SubElement(item, _tag('Isporuka25Ispravak')).text = _money(row.vat_25)
        ET.SubElement(item, _tag('UkupnoOsnovica')).text = _money(row.total_base)
        ET.SubElement(item, _tag('UkupnoIspravak')).text = _money(row.total_vat)

    totals = ET.SubElement(body, _tag('Ukupno'))
    ET.SubElement(totals, _tag('UkupnoOsnovica5')).text = _money(payload.total_base_5)
    ET.SubElement(totals, _tag('UkupnoIspravak5')).text = _money(payload.total_vat_5)
    ET.SubElement(totals, _tag('UkupnoOsnovica13')).text = _money(payload.total_base_13)
    ET.SubElement(totals, _tag('UkupnoIspravak13')).text = _money(payload.total_vat_13)
    ET.SubElement(totals, _tag('UkupnoOsnovica25')).text = _money(payload.total_base_25)
    ET.SubElement(totals, _tag('UkupnoIspravak25')).text = _money(payload.total_vat_25)
    ET.SubElement(totals, _tag('UkupnoOsnovicaUkupno')).text = _money(payload.total_base)
    ET.SubElement(totals, _tag('UkupnoIspravakUkupno')).text = _money(payload.total_vat)

    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
