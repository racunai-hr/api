from __future__ import annotations

import base64
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from lxml import etree

NS = {
    'inv': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}


@dataclass
class ParsedInvoice:
    invoice_number: str
    issue_date: str
    due_date: str | None
    supplier_name: str
    supplier_oib: str
    supplier_address: str
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    line_descriptions: list[str] = field(default_factory=list)


def _text(node, xpath: str, default: str = '') -> str:
    if node is None:
        return default
    found = node.xpath(xpath, namespaces=NS)
    if not found:
        return default
    value = found[0]
    if isinstance(value, etree._Element):
        return (value.text or '').strip()
    return str(value).strip()


def _decimal(value: str, default: str = '0') -> Decimal:
    try:
        return Decimal(str(value).replace(',', '.') or default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def parse_invoice_ubl(ubl_payload: str) -> ParsedInvoice:
    if not ubl_payload.strip().startswith('<'):
        ubl_payload = base64.b64decode(ubl_payload).decode('utf-8')

    root = etree.fromstring(ubl_payload.encode('utf-8'))
    supplier_party = root.xpath('//cac:AccountingSupplierParty/cac:Party', namespaces=NS)
    supplier = supplier_party[0] if supplier_party else root

    supplier_name_el = supplier.xpath(
        './/cac:PartyLegalEntity/cbc:RegistrationName | .//cac:PartyName/cbc:Name',
        namespaces=NS,
    )
    supplier_name = (supplier_name_el[0].text or '').strip() if supplier_name_el else ''
    supplier_oib_el = supplier.xpath(
        './/cbc:EndpointID | .//cac:PartyTaxScheme/cbc:CompanyID | .//cac:PartyLegalEntity/cbc:CompanyID',
        namespaces=NS,
    )
    supplier_oib = (supplier_oib_el[0].text or '').strip() if supplier_oib_el else ''
    supplier_oib = supplier_oib.replace('HR', '').strip()

    street_el = supplier.xpath('.//cac:PostalAddress/cbc:StreetName', namespaces=NS)
    city_el = supplier.xpath('.//cac:PostalAddress/cbc:CityName', namespaces=NS)
    street = (street_el[0].text or '').strip() if street_el else ''
    city = (city_el[0].text or '').strip() if city_el else ''
    supplier_address = ', '.join(part for part in (street, city) if part)

    ids = root.xpath('./cbc:ID', namespaces=NS)
    invoice_number = (ids[0].text or '').strip() if ids else ''
    issue_dates = root.xpath('./cbc:IssueDate', namespaces=NS)
    issue_date = (issue_dates[0].text or '').strip() if issue_dates else ''
    due_dates = root.xpath('./cbc:DueDate', namespaces=NS)
    due_date = (due_dates[0].text or '').strip() if due_dates else None
    currencies = root.xpath('./cbc:DocumentCurrencyCode', namespaces=NS)
    currency = (currencies[0].text or '').strip() if currencies else 'EUR'

    tax_el = root.xpath('//cac:TaxTotal/cbc:TaxAmount', namespaces=NS)
    tax_amount = _decimal((tax_el[0].text or '') if tax_el else '0')
    payable_el = root.xpath('//cac:LegalMonetaryTotal/cbc:PayableAmount', namespaces=NS)
    total_amount = _decimal((payable_el[0].text or '') if payable_el else '0')
    exclusive_el = root.xpath('//cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount', namespaces=NS)
    subtotal = _decimal(
        (exclusive_el[0].text or '') if exclusive_el else '',
        str(total_amount - tax_amount),
    )

    lines = []
    for line in root.xpath('//cac:InvoiceLine', namespaces=NS):
        name_el = line.xpath('.//cac:Item/cbc:Name', namespaces=NS)
        desc_el = line.xpath('.//cac:Item/cbc:Description', namespaces=NS)
        name = (name_el[0].text or '').strip() if name_el else ''
        desc = (desc_el[0].text or '').strip() if desc_el else ''
        lines.append(desc or name or 'Stavka')

    return ParsedInvoice(
        invoice_number=invoice_number or 'UNKNOWN',
        issue_date=issue_date,
        due_date=due_date,
        supplier_name=supplier_name or 'Nepoznat dobavljač',
        supplier_oib=supplier_oib,
        supplier_address=supplier_address,
        currency=currency or 'EUR',
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        line_descriptions=lines,
    )
