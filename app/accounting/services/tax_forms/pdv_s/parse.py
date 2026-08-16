"""Parse Obrazac PDV-S v1.0 XML (unsigned or signed)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from lxml import etree

_OBRAZAC_NS = 'http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacPDVS/v1-0'


@dataclass(frozen=True)
class PdvSParsedRow:
    country_code: str
    pdv_id: str
    goods_amount: Decimal
    services_amount: Decimal


@dataclass(frozen=True)
class PdvSParsedDocument:
    oib: str
    period_from: str
    period_to: str
    rows: tuple[PdvSParsedRow, ...]
    total_goods: Decimal
    total_services: Decimal
    is_signed: bool


def _text(element: etree._Element | None) -> str:
    return (element.text or '').strip() if element is not None else ''


def _decimal(element: etree._Element | None) -> Decimal:
    raw = _text(element).replace(',', '.')
    return Decimal(raw or '0')


def parse_pdv_s_xml(data: bytes) -> PdvSParsedDocument:
    root = etree.fromstring(data)
    ns = f'{{{_OBRAZAC_NS}}}'

    oib = _text(root.find(f'.//{ns}Obveznik/{ns}OIB'))
    period_from = _text(root.find(f'.//{ns}Razdoblje/{ns}DatumOd'))
    period_to = _text(root.find(f'.//{ns}Razdoblje/{ns}DatumDo'))

    rows: list[PdvSParsedRow] = []
    for row in root.findall(f'.//{ns}Isporuka'):
        rows.append(
            PdvSParsedRow(
                country_code=_text(row.find(f'{ns}KodDrzave')),
                pdv_id=_text(row.find(f'{ns}PDVID')),
                goods_amount=_decimal(row.find(f'{ns}I1')),
                services_amount=_decimal(row.find(f'{ns}I2')),
            )
        )

    totals = root.find(f'.//{ns}IsporukeUkupno')
    return PdvSParsedDocument(
        oib=oib,
        period_from=period_from,
        period_to=period_to,
        rows=tuple(rows),
        total_goods=_decimal(totals.find(f'{ns}I1') if totals is not None else None),
        total_services=_decimal(totals.find(f'{ns}I2') if totals is not None else None),
        is_signed=b'SignatureValue' in data,
    )
