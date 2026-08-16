"""Render unsigned Obrazac PDV v11.0 XML from PdvPayload."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from accounting.services.tax_forms.pdv.boxes import VAT_BOX_REGISTRY
from accounting.services.tax_forms.pdv.payload import (
    PdvFieldPair,
    PdvFormHeader,
    PdvMarginPair,
    PdvPayload,
)

_OBRAZAC_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacPDV/v11-0'
_METAPODACI_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'
_DC_TITLE = 'http://purl.org/dc/elements/1.1/title'
_DC_CREATOR = 'http://purl.org/dc/elements/1.1/creator'
_DC_DATE = 'http://purl.org/dc/elements/1.1/date'
_DC_FORMAT = 'http://purl.org/dc/elements/1.1/format'
_DC_LANGUAGE = 'http://purl.org/dc/elements/1.1/language'
_DC_IDENTIFIER = 'http://purl.org/dc/elements/1.1/identifier'
_DC_CONFORMS_TO = 'http://purl.org/dc/terms/conformsTo'
_DC_TYPE = 'http://purl.org/dc/elements/1.1/type'
_MARGIN_BOX_CODES = frozenset({'701', '702', '703', '704'})


def _local(tag: str) -> str:
    return f'{{{_OBRAZAC_NS}}}{tag}'


def _meta(tag: str) -> str:
    return f'{{{_METAPODACI_NS}}}{tag}'


def _append_metapodaci(root: ET.Element, header: PdvFormHeader) -> None:
    metapodaci = ET.SubElement(root, _meta('Metapodaci'))
    author = f'{header.prepared_by_first_name} {header.prepared_by_last_name}'.strip()

    naslov = ET.SubElement(metapodaci, _meta('Naslov'))
    naslov.set('dc', _DC_TITLE)
    naslov.text = 'Obrazac PDV'

    autor = ET.SubElement(metapodaci, _meta('Autor'))
    autor.set('dc', _DC_CREATOR)
    autor.text = author

    datum = ET.SubElement(metapodaci, _meta('Datum'))
    datum.set('dc', _DC_DATE)
    datum.text = datetime.now(timezone.utc).astimezone().isoformat()

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
    uskladjenost.text = 'ObrazacPDV-v11-0'

    tip = ET.SubElement(metapodaci, _meta('Tip'))
    tip.set('dc', _DC_TYPE)
    tip.text = 'Elektronički obrazac'

    adresant = ET.SubElement(metapodaci, _meta('Adresant'))
    adresant.text = 'Ministarstvo Financija, Porezna uprava, Zagreb'


def _format_decimal(value) -> str:
    return f'{value:.2f}'


def _append_pair(parent: ET.Element, code: str, pair: PdvFieldPair) -> None:
    element = ET.SubElement(parent, _local(f'Podatak{code}'))
    ET.SubElement(element, _local('Vrijednost')).text = _format_decimal(pair.vrijednost)
    ET.SubElement(element, _local('Porez')).text = _format_decimal(pair.porez)


def _append_margin(parent: ET.Element, code: str, pair: PdvMarginPair) -> None:
    element = ET.SubElement(parent, _local(f'Podatak{code}'))
    ET.SubElement(element, _local('NabavnaVrijednost')).text = _format_decimal(pair.nabavna_vrijednost)
    ET.SubElement(element, _local('ProdajnaVrijednost')).text = _format_decimal(pair.prodajna_vrijednost)


def _append_scalar(parent: ET.Element, code: str, value) -> None:
    element = ET.SubElement(parent, _local(f'Podatak{code}'))
    element.text = _format_decimal(value)


def _append_bool(parent: ET.Element, code: str, value: bool) -> None:
    element = ET.SubElement(parent, _local(f'Podatak{code}'))
    element.text = 'true' if value else 'false'


def _append_body(parent: ET.Element, payload: PdvPayload) -> None:
    for definition in VAT_BOX_REGISTRY:
        code = definition.code
        if definition.field_type == 'pair':
            if code in _MARGIN_BOX_CODES:
                _append_margin(parent, code, payload.field_margin(code))
            else:
                _append_pair(parent, code, payload.field_pair(code))
        elif definition.field_type == 'scalar':
            _append_scalar(parent, code, payload.field_scalar(code))
        else:
            _append_bool(parent, code, payload.field_bool(code))

    ET.SubElement(parent, _local('Povrat')).text = '0.00'
    ET.SubElement(parent, _local('PodaciZaUstup'))
    ET.SubElement(parent, _local('Predujam')).text = '0.00'
    ET.SubElement(parent, _local('UstupPovrata')).text = '0.00'


def render_pdv_obrazac_xml(payload: PdvPayload, *, header: PdvFormHeader) -> bytes:
    root = ET.Element(_local('ObrazacPDV'), {'verzijaSheme': payload.schema_version})
    root.set('xmlns', _OBRAZAC_NS)

    _append_metapodaci(root, header)

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
    ET.SubElement(obracun, _local('Ime')).text = header.prepared_by_first_name
    ET.SubElement(obracun, _local('Prezime')).text = header.prepared_by_last_name
    ET.SubElement(zaglavlje, _local('Ispostava')).text = header.tax_office_code

    tijelo = ET.SubElement(root, _local('Tijelo'))
    _append_body(tijelo, payload)

    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
