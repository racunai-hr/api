"""Render unsigned draft Obrazac JOPPD XML from JoppdPayload.

Vidi napomenu u ``domains/tax/forms/joppd/__init__.py`` — ova shema NIJE
validirana protiv službenog JOPPD XSD-a Porezne uprave (za razliku od ZP/PDV
koji imaju lokalnu kopiju sheme). Struktura (Stranica A zaglavlje, Stranica B
redovi po stjecatelju) slijedi javno objavljenu specifikaciju obrasca, ali
prije stvarne predaje potrebna je provjera protiv važeće XSD verzije.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from uuid import uuid4

from domains.tax.forms.joppd.payload import JOPPD_SCHEMA_VERSION, JoppdPayload

_OBRAZAC_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacJOPPD/v1-0-draft'
_METAPODACI_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'
_DC_TITLE = 'http://purl.org/dc/elements/1.1/title'
_DC_CREATOR = 'http://purl.org/dc/elements/1.1/creator'
_DC_DATE = 'http://purl.org/dc/elements/1.1/date'
_DC_FORMAT = 'http://purl.org/dc/elements/1.1/format'
_DC_LANGUAGE = 'http://purl.org/dc/elements/1.1/language'
_DC_IDENTIFIER = 'http://purl.org/dc/elements/1.1/identifier'
_DC_CONFORMS_TO = 'http://purl.org/dc/terms/conformsTo'
_DC_TYPE = 'http://purl.org/dc/elements/1.1/type'
_JOPPD_TITLE = (
    'Izvješće o primicima, porezu na dohodak i prirezu te doprinosima za '
    'obvezna osiguranja (JOPPD)'
)


def _local(tag: str) -> str:
    return f'{{{_OBRAZAC_NS}}}{tag}'


def _meta(tag: str) -> str:
    return f'{{{_METAPODACI_NS}}}{tag}'


def _format_decimal(value) -> str:
    return f'{value:.2f}'


def _append_metapodaci(root: ET.Element, payload: JoppdPayload) -> None:
    author = f'{payload.prepared_by.first_name} {payload.prepared_by.last_name}'.strip()
    metapodaci = ET.SubElement(root, _meta('Metapodaci'))

    naslov = ET.SubElement(metapodaci, _meta('Naslov'))
    naslov.set('dc', _DC_TITLE)
    naslov.text = _JOPPD_TITLE

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
    uskladjenost.text = 'ObrazacJOPPD-v1-0-draft'

    tip = ET.SubElement(metapodaci, _meta('Tip'))
    tip.set('dc', _DC_TYPE)
    tip.text = 'Elektronički obrazac'

    adresant = ET.SubElement(metapodaci, _meta('Adresant'))
    adresant.text = 'Ministarstvo Financija, Porezna uprava, Zagreb'


def render_joppd_xml(payload: JoppdPayload) -> bytes:
    """Render unsigned draft Obrazac JOPPD XML from canonical payload."""
    root = ET.Element(
        _local('ObrazacJOPPD'),
        {'verzijaSheme': JOPPD_SCHEMA_VERSION},
    )
    root.set('xmlns', _OBRAZAC_NS)

    _append_metapodaci(root, payload)

    # --- Stranica A — zaglavlje -------------------------------------------------
    stranica_a = ET.SubElement(root, _local('StranicaA'))
    razdoblje = ET.SubElement(stranica_a, _local('Razdoblje'))
    ET.SubElement(razdoblje, _local('Godina')).text = str(payload.period_year)
    ET.SubElement(razdoblje, _local('Mjesec')).text = f'{payload.period_month:02d}'
    ET.SubElement(razdoblje, _local('DatumIsplate')).text = payload.payment_date.isoformat()
    ET.SubElement(razdoblje, _local('OznakaIzvjesca')).text = payload.report_id
    ET.SubElement(stranica_a, _local('VrstaIzvjesca')).text = '1'  # 1 = redovno

    poslodavac = ET.SubElement(stranica_a, _local('Poslodavac'))
    ET.SubElement(poslodavac, _local('Naziv')).text = payload.employer.name
    ET.SubElement(poslodavac, _local('OIB')).text = payload.employer.oib
    adresa = ET.SubElement(poslodavac, _local('Adresa'))
    ET.SubElement(adresa, _local('Mjesto')).text = payload.employer.city
    ET.SubElement(adresa, _local('Ulica')).text = payload.employer.street
    ET.SubElement(adresa, _local('Broj')).text = payload.employer.house_number

    obracun = ET.SubElement(stranica_a, _local('ObracunSastavio'))
    ET.SubElement(obracun, _local('Ime')).text = payload.prepared_by.first_name
    ET.SubElement(obracun, _local('Prezime')).text = payload.prepared_by.last_name
    if payload.prepared_by.phone:
        ET.SubElement(obracun, _local('Telefon')).text = payload.prepared_by.phone
    if payload.prepared_by.email:
        ET.SubElement(obracun, _local('Email')).text = payload.prepared_by.email

    ukupno_a = ET.SubElement(stranica_a, _local('ZbirniPodaci'))
    ET.SubElement(ukupno_a, _local('BrojStjecatelja')).text = str(len(payload.rows))
    ET.SubElement(ukupno_a, _local('UkupnoBruto')).text = _format_decimal(payload.total_gross)
    ET.SubElement(ukupno_a, _local('UkupnoMIO1')).text = _format_decimal(payload.total_mio1)
    ET.SubElement(ukupno_a, _local('UkupnoMIO2')).text = _format_decimal(payload.total_mio2)
    ET.SubElement(ukupno_a, _local('UkupnoPorez')).text = _format_decimal(payload.total_income_tax)
    ET.SubElement(ukupno_a, _local('UkupnoZO')).text = _format_decimal(payload.total_zo)
    ET.SubElement(ukupno_a, _local('UkupnoNeto')).text = _format_decimal(payload.total_net)

    # --- Stranica B/C — redovi po stjecatelju -----------------------------------
    stranica_b = ET.SubElement(root, _local('StranicaB'))
    for index, row in enumerate(payload.rows, start=1):
        stjecatelj = ET.SubElement(stranica_b, _local('Stjecatelj'))
        ET.SubElement(stjecatelj, _local('RedBr')).text = str(index)
        ET.SubElement(stjecatelj, _local('OIB')).text = row.recipient_oib
        ET.SubElement(stjecatelj, _local('Ime')).text = row.first_name
        ET.SubElement(stjecatelj, _local('Prezime')).text = row.last_name
        if row.date_of_birth:
            ET.SubElement(stjecatelj, _local('DatumRodenja')).text = row.date_of_birth.isoformat()
        prebivaliste = ET.SubElement(stjecatelj, _local('Prebivaliste'))
        ET.SubElement(prebivaliste, _local('SifraOpcine')).text = row.municipality_code
        ET.SubElement(prebivaliste, _local('Mjesto')).text = row.city
        ET.SubElement(prebivaliste, _local('Ulica')).text = row.street
        if row.work_location:
            ET.SubElement(stjecatelj, _local('MjestoRada')).text = row.work_location

        oznake = ET.SubElement(stjecatelj, _local('Oznake'))
        ET.SubElement(oznake, _local('OznakaStjecatelja')).text = row.recipient_code
        ET.SubElement(oznake, _local('OznakaPrimitka')).text = row.income_type_code
        ET.SubElement(oznake, _local('PrviMjesec')).text = '1' if row.is_first_month else '0'
        ET.SubElement(oznake, _local('ZadnjiMjesec')).text = '1' if row.is_last_month else '0'

        obracun_row = ET.SubElement(stjecatelj, _local('Obracun'))
        ET.SubElement(obracun_row, _local('Razdoblje')).text = (
            f'{row.date_from.isoformat()}/{row.date_to.isoformat()}'
        )
        ET.SubElement(obracun_row, _local('Sati')).text = _format_decimal(row.hours_total)
        ET.SubElement(obracun_row, _local('Bruto')).text = _format_decimal(row.gross_salary)
        ET.SubElement(obracun_row, _local('OsnovicaMIO1')).text = _format_decimal(row.mio1_base)
        ET.SubElement(obracun_row, _local('MIO1')).text = _format_decimal(row.mio1_amount)
        if row.mio_pillar_both:
            ET.SubElement(obracun_row, _local('MIO2')).text = _format_decimal(row.mio2_amount)
        ET.SubElement(obracun_row, _local('Dohodak')).text = _format_decimal(row.income_base)
        ET.SubElement(obracun_row, _local('OsobniOdbitak')).text = _format_decimal(
            row.personal_allowance_amount
        )
        ET.SubElement(obracun_row, _local('PoreznaOsnovica')).text = _format_decimal(row.tax_base)
        ET.SubElement(obracun_row, _local('StopaPoreza')).text = _format_decimal(
            row.income_tax_rate_used
        )
        ET.SubElement(obracun_row, _local('Porez')).text = _format_decimal(row.income_tax_amount)
        ET.SubElement(obracun_row, _local('Prirez')).text = '0.00'
        ET.SubElement(obracun_row, _local('Neto')).text = _format_decimal(row.net_salary)
        ET.SubElement(obracun_row, _local('ZO')).text = _format_decimal(row.zo_amount)
        ET.SubElement(obracun_row, _local('UkupanTrosak')).text = _format_decimal(
            row.total_labor_cost
        )

    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
