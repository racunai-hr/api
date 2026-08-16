"""Parse submitted Obrazac PDV XML into PdvPayload."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

from accounting.services.tax_forms.pdv.boxes import active_boxes
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING_VERSION
from accounting.services.tax_forms.pdv.payload import (
    PDV_SCHEMA_VERSION,
    PdvFieldPair,
    PdvMarginPair,
    PdvPayload,
    TaxpayerInfo,
    freeze_fields,
)

_OBRAZAC_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacPDV/v11-0'
_MARGIN_BOX_CODES = frozenset({'701', '702', '703', '704'})


def _local(tag: str) -> str:
    return f'{{{_OBRAZAC_NS}}}{tag}'


def _parse_decimal(text: str | None) -> Decimal:
    if not text:
        return Decimal('0.00')
    try:
        return Decimal(text.strip()).quantize(Decimal('0.01'))
    except InvalidOperation as exc:
        raise ValueError(f'Invalid decimal in PDV XML: {text!r}') from exc


def _parse_date(text: str | None) -> date:
    if not text:
        raise ValueError('PDV XML missing date value')
    return date.fromisoformat(text.strip())


def _parse_taxpayer(header: ET.Element) -> TaxpayerInfo:
    taxpayer = header.find(_local('Obveznik'))
    if taxpayer is None:
        raise ValueError('Obrazac PDV XML missing Obveznik element')
    address = taxpayer.find(_local('Adresa'))
    return TaxpayerInfo(
        oib=(taxpayer.findtext(_local('OIB')) or '').strip(),
        name=(taxpayer.findtext(_local('Naziv')) or '').strip(),
        street=(address.findtext(_local('Ulica')) or '').strip() if address is not None else '',
        house_number=(address.findtext(_local('Broj')) or '').strip() if address is not None else '',
        city=(address.findtext(_local('Mjesto')) or '').strip() if address is not None else '',
        postal_code='',
    )


def _parse_period(header: ET.Element) -> tuple[date, date]:
    period = header.find(_local('Razdoblje'))
    if period is None:
        raise ValueError('Obrazac PDV XML missing Razdoblje element')
    return _parse_date(period.findtext(_local('DatumOd'))), _parse_date(period.findtext(_local('DatumDo')))


def _parse_field(element: ET.Element, definition) -> PdvFieldPair | PdvMarginPair | Decimal | bool:
    if definition.field_type == 'pair':
        if definition.code in _MARGIN_BOX_CODES:
            return PdvMarginPair(
                nabavna_vrijednost=_parse_decimal(element.findtext(_local('NabavnaVrijednost'))),
                prodajna_vrijednost=_parse_decimal(element.findtext(_local('ProdajnaVrijednost'))),
            )
        return PdvFieldPair(
            vrijednost=_parse_decimal(element.findtext(_local('Vrijednost'))),
            porez=_parse_decimal(element.findtext(_local('Porez'))),
        )
    if definition.field_type == 'scalar':
        return _parse_decimal(element.text)
    return (element.text or '').strip().lower() == 'true'


def parse_pdv_obrazac_xml(data: bytes) -> PdvPayload:
    root = ET.fromstring(data)
    header = root.find(_local('Zaglavlje'))
    body = root.find(_local('Tijelo'))
    if header is None:
        raise ValueError('Obrazac PDV XML missing Zaglavlje element')
    if body is None:
        raise ValueError('Obrazac PDV XML missing Tijelo element')

    period_from, period_to = _parse_period(header)
    schema_version = root.attrib.get('verzijaSheme', PDV_SCHEMA_VERSION)

    fields: dict[str, PdvFieldPair | PdvMarginPair | Decimal | bool] = {}
    for definition in active_boxes():
        element = body.find(_local(f'Podatak{definition.code}'))
        if element is None:
            if definition.field_type == 'pair':
                if definition.code in _MARGIN_BOX_CODES:
                    fields[definition.code] = PdvMarginPair(Decimal('0.00'), Decimal('0.00'))
                else:
                    fields[definition.code] = PdvFieldPair(Decimal('0.00'), Decimal('0.00'))
            elif definition.field_type == 'scalar':
                fields[definition.code] = Decimal('0.00')
            else:
                fields[definition.code] = False
            continue
        fields[definition.code] = _parse_field(element, definition)

    return PdvPayload(
        schema_version=schema_version,
        mapping_version=PDV_MAPPING_VERSION,
        period_from=period_from,
        period_to=period_to,
        taxpayer=_parse_taxpayer(header),
        fields=freeze_fields(fields),
    )
