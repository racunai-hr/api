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


class UblMonetaryError(ValueError):
    """UBL LegalMonetaryTotal cannot yield a document gross amount."""


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
    payable_amount: Decimal = Decimal('0')
    prepaid_amount: Decimal = Decimal('0')
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


def _optional_decimal(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return Decimal(text.replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None


def _resolve_total_amount(
    *,
    inclusive: Decimal | None,
    exclusive: Decimal | None,
    tax_amount: Decimal | None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (total_amount, subtotal, tax_amount). Fail if gross cannot be proven."""
    tax = tax_amount if tax_amount is not None else Decimal('0')

    if inclusive is not None:
        subtotal = exclusive if exclusive is not None else inclusive - tax
        return inclusive, subtotal, tax

    if exclusive is not None and tax_amount is not None:
        return exclusive + tax_amount, exclusive, tax_amount

    raise UblMonetaryError(
        'UBL LegalMonetaryTotal missing TaxInclusiveAmount and cannot fall back '
        'to TaxExclusiveAmount + TaxAmount.'
    )


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
    inclusive_el = root.xpath('//cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount', namespaces=NS)
    exclusive_el = root.xpath('//cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount', namespaces=NS)
    payable_el = root.xpath('//cac:LegalMonetaryTotal/cbc:PayableAmount', namespaces=NS)
    prepaid_el = root.xpath('//cac:LegalMonetaryTotal/cbc:PrepaidAmount', namespaces=NS)

    tax_raw = (tax_el[0].text if tax_el else None)
    inclusive = _optional_decimal(inclusive_el[0].text if inclusive_el else None)
    exclusive = _optional_decimal(exclusive_el[0].text if exclusive_el else None)
    tax_parsed = _optional_decimal(tax_raw)

    total_amount, subtotal, tax_amount = _resolve_total_amount(
        inclusive=inclusive,
        exclusive=exclusive,
        tax_amount=tax_parsed,
    )
    payable_amount = _optional_decimal(payable_el[0].text if payable_el else None) or Decimal('0')
    prepaid_amount = _optional_decimal(prepaid_el[0].text if prepaid_el else None) or Decimal('0')

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
        payable_amount=payable_amount,
        prepaid_amount=prepaid_amount,
        line_descriptions=lines,
    )
