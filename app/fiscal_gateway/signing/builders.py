from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from lxml import etree

from fiscal_gateway.constants import NS_EFISKALIZACIJA
from fiscal_gateway.signing.xades import fiscal_timestamp, new_request_id


@dataclass
class FiscalParty:
    name: str
    oib: str


@dataclass
class FiscalIssuer(FiscalParty):
    operator_oib: str


@dataclass
class FiscalVatBreakdown:
    category: str = 'S'
    taxable_amount: str = '100.00'
    tax_amount: str = '25.00'
    rate: str = '25'


@dataclass
class FiscalLineItem:
    quantity: str = '1.000'
    unit: str = 'H87'
    line_net: str = '100.00'
    net_price: str = '100.000000'
    base_quantity: str = '1.000'
    base_unit: str = 'H87'
    vat_category: str = 'S'
    vat_rate: str = '25'
    name: str = 'Proizvod'
    classification_id: str = '62.90.90'
    classification_scheme: str = 'CG'
    classification_version: str = '1'


@dataclass
class FiscalDocumentTotals:
    net: str = '100.00'
    amount_without_vat: str = '100.00'
    vat: str = '25.00'
    amount_with_vat: str = '125.00'
    payable: str = '125.00'


@dataclass
class OutgoingInvoicePayload:
    document_number: str
    issue_date: str
    issuer: FiscalIssuer
    recipient: FiscalParty
    document_type: str = '380'
    currency: str = 'EUR'
    due_date: str = ''
    business_process: str = 'P1'
    invoice_kind: str = 'I'
    payment_account: str = ''
    totals: FiscalDocumentTotals = field(default_factory=FiscalDocumentTotals)
    vat_breakdown: FiscalVatBreakdown = field(default_factory=FiscalVatBreakdown)
    line_items: list[FiscalLineItem] = field(default_factory=lambda: [FiscalLineItem()])
    copy_indicator: bool = False
    request_id: str = field(default_factory=new_request_id)


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f'{{{NS_EFISKALIZACIJA}}}{tag}')
    if text is not None:
        el.text = text
    return el


def build_evidentiraj_eracun_zahtjev(payload: OutgoingInvoicePayload) -> etree._Element:
    root = etree.Element(
        f'{{{NS_EFISKALIZACIJA}}}EvidentirajERacunZahtjev',
        nsmap={'fis': NS_EFISKALIZACIJA},
    )
    root.set(f'{{{NS_EFISKALIZACIJA}}}id', payload.request_id)

    header = _sub(root, 'Zaglavlje')
    _sub(header, 'datumVrijemeSlanja', fiscal_timestamp())
    _sub(header, 'vrstaERacuna', payload.invoice_kind)

    invoice = _sub(root, 'ERacun')
    _sub(invoice, 'brojDokumenta', payload.document_number)
    _sub(invoice, 'datumIzdavanja', payload.issue_date)
    _sub(invoice, 'vrstaDokumenta', payload.document_type)
    _sub(invoice, 'valutaERacuna', payload.currency)
    if payload.due_date:
        _sub(invoice, 'datumDospijecaPlacanja', payload.due_date)
    _sub(invoice, 'vrstaPoslovnogProcesa', payload.business_process)

    issuer = _sub(invoice, 'Izdavatelj')
    _sub(issuer, 'ime', payload.issuer.name)
    _sub(issuer, 'oibPorezniBroj', payload.issuer.oib)
    _sub(issuer, 'oibOperatera', payload.issuer.operator_oib)

    recipient = _sub(invoice, 'Primatelj')
    _sub(recipient, 'ime', payload.recipient.name)
    _sub(recipient, 'oibPorezniBroj', payload.recipient.oib)

    if payload.payment_account:
        transfer = _sub(invoice, 'PrijenosSredstava')
        _sub(transfer, 'identifikatorRacunaZaPlacanje', payload.payment_account)

    totals = _sub(invoice, 'DokumentUkupanIznos')
    _sub(totals, 'neto', payload.totals.net)
    _sub(totals, 'iznosBezPdv', payload.totals.amount_without_vat)
    _sub(totals, 'pdv', payload.totals.vat)
    _sub(totals, 'iznosSPdv', payload.totals.amount_with_vat)
    _sub(totals, 'iznosKojiDospijevaZaPlacanje', payload.totals.payable)

    vat = _sub(invoice, 'RaspodjelaPdv')
    _sub(vat, 'kategorijaPdv', payload.vat_breakdown.category)
    _sub(vat, 'oporeziviIznos', payload.vat_breakdown.taxable_amount)
    _sub(vat, 'iznosPoreza', payload.vat_breakdown.tax_amount)
    _sub(vat, 'stopa', payload.vat_breakdown.rate)

    for item in payload.line_items:
        line = _sub(invoice, 'StavkaERacuna')
        _sub(line, 'kolicina', item.quantity)
        _sub(line, 'jedinicaMjere', item.unit)
        _sub(line, 'neto', item.line_net)
        _sub(line, 'artiklNetoCijena', item.net_price)
        _sub(line, 'artiklOsnovnaKolicina', item.base_quantity)
        _sub(line, 'artiklJedinicaMjereZaOsnovnuKolicinu', item.base_unit)
        _sub(line, 'artiklKategorijaPdv', item.vat_category)
        _sub(line, 'artiklStopaPdv', item.vat_rate)
        _sub(line, 'artiklNaziv', item.name)
        classification = _sub(line, 'ArtiklIdentifikatorKlasifikacija')
        _sub(classification, 'identifikatorKlasifikacije', item.classification_id)
        _sub(classification, 'identifikatorSheme', item.classification_scheme)
        _sub(classification, 'verzijaSheme', item.classification_version)

    _sub(invoice, 'indikatorKopije', 'true' if payload.copy_indicator else 'false')
    return root


def demo_payload_from_options(options: dict[str, Any]) -> OutgoingInvoicePayload:
    return OutgoingInvoicePayload(
        document_number=options.get('document_number', '1-P1-1'),
        issue_date=options.get('issue_date', '2026-06-12'),
        due_date=options.get('due_date', '2026-07-12'),
        issuer=FiscalIssuer(
            name=options.get('issuer_name', 'Fine Star d.o.o.'),
            oib=options.get('issuer_oib', '36619131370'),
            operator_oib=options.get('operator_oib', '11528564544'),
        ),
        recipient=FiscalParty(
            name=options.get('recipient_name', 'Test kupac d.o.o.'),
            oib=options.get('recipient_oib', '11111111119'),
        ),
        payment_account=options.get('payment_account', ''),
        business_process=options.get('business_process', 'P1'),
        invoice_kind=options.get('invoice_kind', 'I'),
    )


def format_decimal(value: Decimal | str, places: int = 2) -> str:
    if isinstance(value, str):
        value = Decimal(value)
    return f'{value:.{places}f}'
