"""Render unsigned Obrazac TZ2 v1.0 XML from Tz2Payload."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from uuid import uuid4

from accounting.services.tax_forms.tz2.payload import TZ2_SCHEMA_VERSION, Tz2Payload

_OBRAZAC_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacTZ2/v1-0'
_METAPODACI_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'
_DC_TITLE = 'http://purl.org/dc/elements/1.1/title'
_DC_CREATOR = 'http://purl.org/dc/elements/1.1/creator'
_DC_DATE = 'http://purl.org/dc/elements/1.1/date'
_DC_FORMAT = 'http://purl.org/dc/elements/1.1/format'
_DC_LANGUAGE = 'http://purl.org/dc/elements/1.1/language'
_DC_IDENTIFIER = 'http://purl.org/dc/elements/1.1/identifier'
_DC_CONFORMS_TO = 'http://purl.org/dc/terms/conformsTo'
_DC_TYPE = 'http://purl.org/dc/elements/1.1/type'
_TZ2_TITLE = 'Obrazac TZ 2'


def _local(tag: str) -> str:
    return f'{{{_OBRAZAC_NS}}}{tag}'


def _meta(tag: str) -> str:
    return f'{{{_METAPODACI_NS}}}{tag}'


def _format_decimal(value) -> str:
    return f'{value:.2f}'


def _append_metapodaci(root: ET.Element, payload: Tz2Payload) -> None:
    author = f'{payload.prepared_by.first_name} {payload.prepared_by.last_name}'.strip()
    metapodaci = ET.SubElement(root, _meta('Metapodaci'))

    naslov = ET.SubElement(metapodaci, _meta('Naslov'))
    naslov.set('dc', _DC_TITLE)
    naslov.text = _TZ2_TITLE

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
    uskladjenost.text = 'ObrazacTZ2-v1-0'

    tip = ET.SubElement(metapodaci, _meta('Tip'))
    tip.set('dc', _DC_TYPE)
    tip.text = 'Elektronički obrazac'

    adresant = ET.SubElement(metapodaci, _meta('Adresant'))
    adresant.text = 'Ministarstvo Financija, Porezna uprava, Zagreb'


def render_tz2_xml(payload: Tz2Payload) -> bytes:
    """Render unsigned Obrazac TZ 2 XML from canonical payload."""
    ET.register_namespace('', _OBRAZAC_NS)
    ET.register_namespace('meta', _METAPODACI_NS)

    root = ET.Element(_local('ObrazacTZ2'), {'verzijaSheme': TZ2_SCHEMA_VERSION})
    _append_metapodaci(root, payload)

    zaglavlje = ET.SubElement(root, _local('Zaglavlje'))
    obveznik = ET.SubElement(zaglavlje, _local('Obveznik'))
    ET.SubElement(obveznik, _local('Ime')).text = payload.taxpayer.first_name
    ET.SubElement(obveznik, _local('Prezime')).text = payload.taxpayer.last_name
    ET.SubElement(obveznik, _local('OIB')).text = payload.taxpayer.oib
    adresa = ET.SubElement(obveznik, _local('Adresa'))
    ET.SubElement(adresa, _local('SifraOpcine')).text = payload.taxpayer.municipality_code
    ET.SubElement(adresa, _local('Mjesto')).text = payload.taxpayer.city
    ET.SubElement(adresa, _local('Ulica')).text = payload.taxpayer.street
    ET.SubElement(adresa, _local('Broj')).text = payload.taxpayer.house_number

    razdoblje = ET.SubElement(zaglavlje, _local('Razdoblje'))
    ET.SubElement(razdoblje, _local('DatumOd')).text = payload.period_from.isoformat()
    ET.SubElement(razdoblje, _local('DatumDo')).text = payload.period_to.isoformat()

    tijelo = ET.SubElement(root, _local('Tijelo'))
    fields = {
        'Podatak01': str(payload.room_beds),
        'Podatak02': _format_decimal(payload.room_bed_rate),
        'Podatak03': _format_decimal(payload.room_bed_total),
        'Podatak04': str(payload.aux_beds),
        'Podatak05': _format_decimal(payload.aux_bed_rate),
        'Podatak06': _format_decimal(payload.aux_bed_total),
        'Podatak07': str(payload.camp_units),
        'Podatak08': _format_decimal(payload.camp_rate),
        'Podatak09': _format_decimal(payload.camp_total),
        'Podatak10': str(payload.robinson_units),
        'Podatak11': _format_decimal(payload.robinson_rate),
        'Podatak12': _format_decimal(payload.robinson_total),
        'Podatak13': str(payload.opg_room_beds),
        'Podatak14': _format_decimal(payload.opg_room_bed_rate),
        'Podatak15': _format_decimal(payload.opg_room_bed_total),
        'Podatak16': str(payload.opg_aux_beds),
        'Podatak17': _format_decimal(payload.opg_aux_bed_rate),
        'Podatak18': _format_decimal(payload.opg_aux_bed_total),
        'Podatak19': str(payload.opg_camp_units),
        'Podatak20': _format_decimal(payload.opg_camp_rate),
        'Podatak21': _format_decimal(payload.opg_camp_total),
        'Podatak22': str(payload.opg_robinson_units),
        'Podatak23': _format_decimal(payload.opg_robinson_rate),
        'Podatak24': _format_decimal(payload.opg_robinson_total),
        'Podatak25': _format_decimal(payload.total_assessed),
        'Podatak26': _format_decimal(payload.discount_group_1),
        'Podatak27': _format_decimal(payload.discount_group_2),
        'Podatak28': _format_decimal(payload.discount_group_3),
        'Podatak29': _format_decimal(payload.discount_group_4),
        'Podatak30': _format_decimal(payload.total_discount),
        'Podatak31': _format_decimal(payload.amount_after_discount),
        'Podatak32': payload.lump_sum_flag,
        'Podatak33': _format_decimal(payload.lump_sum_amount),
        'Podatak34': payload.installment_flag,
        'Podatak35': _format_decimal(payload.installment_amount),
        'Podatak36': _format_decimal(payload.ep_receipts),
    }
    for name, value in fields.items():
        ET.SubElement(tijelo, _local(name)).text = value

    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
