from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from lxml import etree

NS = {
    'inv': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}

# Leading numeric token in AllowanceChargeReason (generic UBL reason text pattern).
_REASON_CODE_RE = re.compile(r'^(\d{3,})\b')

PARSER_VERSION = 'ubl.parser.invoice/2'


class UblMonetaryError(ValueError):
    """UBL LegalMonetaryTotal cannot yield a document gross amount."""


@dataclass(frozen=True)
class ParsedClassification:
    scheme: str | None
    code: str | None


@dataclass(frozen=True)
class ParsedLine:
    line_id: str | None
    position: int
    name: str | None
    description: str | None
    quantity: Decimal | None
    unit_code: str | None
    unit_price: Decimal | None
    line_extension_amount: Decimal | None
    vat_rate: Decimal | None
    classification: ParsedClassification | None


@dataclass(frozen=True)
class ParsedCharge:
    charge_indicator: bool | None
    code: str | None
    description: str | None
    amount: Decimal | None
    vat_rate: Decimal | None


@dataclass(frozen=True)
class ParsedTaxSummaryRow:
    rate: Decimal | None
    base: Decimal | None
    vat: Decimal | None
    category_id: str | None


@dataclass(frozen=True)
class ParsedReference:
    type: str
    value: str


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
    # Structured extensions (generic UBL; absent → empty / null).
    profile_id: str | None = None
    customization_id: str | None = None
    delivery_date: str | None = None
    notes: list[str] = field(default_factory=list)
    supplier_vat_id: str | None = None
    supplier_country_code: str | None = None
    supplier_street: str | None = None
    supplier_city: str | None = None
    supplier_postal_code: str | None = None
    line_extension_amount: Decimal | None = None
    charge_total_amount: Decimal | None = None
    allowance_total_amount: Decimal | None = None
    lines: list[ParsedLine] = field(default_factory=list)
    charges: list[ParsedCharge] = field(default_factory=list)
    tax_summary: list[ParsedTaxSummaryRow] = field(default_factory=list)
    references: list[ParsedReference] = field(default_factory=list)
    parser_version: str = PARSER_VERSION


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


def _first_text(root, xpath: str) -> str | None:
    value = _text(root, xpath, default='')
    return value or None


def _reason_code(reason: str | None, reason_code: str | None) -> str | None:
    if reason_code:
        return reason_code.strip() or None
    if not reason:
        return None
    match = _REASON_CODE_RE.match(reason.strip())
    return match.group(1) if match else None


def _parse_lines(root) -> tuple[list[ParsedLine], list[str]]:
    lines: list[ParsedLine] = []
    descriptions: list[str] = []
    for index, line in enumerate(root.xpath('//cac:InvoiceLine', namespaces=NS), start=1):
        name = _first_text(line, './/cac:Item/cbc:Name')
        desc = _first_text(line, './/cac:Item/cbc:Description')
        descriptions.append(desc or name or 'Stavka')

        qty_els = line.xpath('./cbc:InvoicedQuantity', namespaces=NS)
        quantity = _optional_decimal(qty_els[0].text if qty_els else None)
        unit_code = qty_els[0].get('unitCode') if qty_els else None

        classif = None
        classif_els = line.xpath('.//cac:CommodityClassification/cbc:ItemClassificationCode', namespaces=NS)
        if classif_els:
            code_el = classif_els[0]
            code = (code_el.text or '').strip() or None
            scheme = (code_el.get('listID') or code_el.get('listAgencyID') or '').strip() or None
            if code or scheme:
                classif = ParsedClassification(scheme=scheme, code=code)

        lines.append(
            ParsedLine(
                line_id=_first_text(line, './cbc:ID'),
                position=index,
                name=name,
                description=desc,
                quantity=quantity,
                unit_code=unit_code,
                unit_price=_optional_decimal(
                    _text(line, './/cac:Price/cbc:PriceAmount', default='') or None
                ),
                line_extension_amount=_optional_decimal(
                    _text(line, './cbc:LineExtensionAmount', default='') or None
                ),
                vat_rate=_optional_decimal(
                    _text(line, './/cac:ClassifiedTaxCategory/cbc:Percent', default='') or None
                ),
                classification=classif,
            )
        )
    return lines, descriptions


def _parse_charges(root) -> list[ParsedCharge]:
    charges: list[ParsedCharge] = []
    for ac in root.xpath('./cac:AllowanceCharge', namespaces=NS):
        indicator_raw = _first_text(ac, './cbc:ChargeIndicator')
        indicator = None
        if indicator_raw is not None:
            indicator = indicator_raw.lower() in ('true', '1')
        reason = _first_text(ac, './cbc:AllowanceChargeReason')
        reason_code = _first_text(ac, './cbc:AllowanceChargeReasonCode')
        charges.append(
            ParsedCharge(
                charge_indicator=indicator,
                code=_reason_code(reason, reason_code),
                description=reason,
                amount=_optional_decimal(_text(ac, './cbc:Amount', default='') or None),
                vat_rate=_optional_decimal(
                    _text(ac, './/cac:TaxCategory/cbc:Percent', default='') or None
                ),
            )
        )
    return charges


def _parse_tax_summary(root) -> list[ParsedTaxSummaryRow]:
    rows: list[ParsedTaxSummaryRow] = []
    for st in root.xpath('//cac:TaxTotal/cac:TaxSubtotal', namespaces=NS):
        rows.append(
            ParsedTaxSummaryRow(
                rate=_optional_decimal(_text(st, './/cbc:Percent', default='') or None),
                base=_optional_decimal(_text(st, './cbc:TaxableAmount', default='') or None),
                vat=_optional_decimal(_text(st, './cbc:TaxAmount', default='') or None),
                category_id=_first_text(st, './/cbc:ID'),
            )
        )
    return rows


def _parse_references(root) -> list[ParsedReference]:
    """Collect common UBL document references; skip empty values."""
    mapping = [
        ('order', './cac:OrderReference/cbc:ID'),
        ('billing', './cac:BillingReference//cac:InvoiceDocumentReference/cbc:ID'),
        ('contract', './cac:ContractDocumentReference/cbc:ID'),
        ('despatch', './cac:DespatchDocumentReference/cbc:ID'),
        ('receipt', './cac:ReceiptDocumentReference/cbc:ID'),
        ('originator', './cac:OriginatorDocumentReference/cbc:ID'),
        ('additional', './cac:AdditionalDocumentReference/cbc:ID'),
        ('project', './cac:ProjectReference/cbc:ID'),
        ('buyer', './cac:BuyerReference'),
        ('payment', './cac:PaymentMeans/cbc:PaymentID'),
    ]
    refs: list[ParsedReference] = []
    for ref_type, xpath in mapping:
        for el in root.xpath(xpath, namespaces=NS):
            value = (el.text or '').strip()
            if value:
                refs.append(ParsedReference(type=ref_type, value=value))
    return refs


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
    supplier_oib_raw = (supplier_oib_el[0].text or '').strip() if supplier_oib_el else ''
    supplier_vat_id = None
    tax_ids = supplier.xpath('.//cac:PartyTaxScheme/cbc:CompanyID', namespaces=NS)
    if tax_ids and (tax_ids[0].text or '').strip():
        supplier_vat_id = (tax_ids[0].text or '').strip()
    supplier_oib = supplier_oib_raw.replace('HR', '').strip()

    street = _first_text(supplier, './/cac:PostalAddress/cbc:StreetName')
    city = _first_text(supplier, './/cac:PostalAddress/cbc:CityName')
    postal = _first_text(supplier, './/cac:PostalAddress/cbc:PostalZone')
    country = _first_text(supplier, './/cac:PostalAddress/cac:Country/cbc:IdentificationCode')
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

    lines, line_descriptions = _parse_lines(root)
    notes = [
        (el.text or '').strip()
        for el in root.xpath('./cbc:Note', namespaces=NS)
        if (el.text or '').strip()
    ]

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
        line_descriptions=line_descriptions,
        profile_id=_first_text(root, './cbc:ProfileID'),
        customization_id=_first_text(root, './cbc:CustomizationID'),
        delivery_date=_first_text(root, './cac:Delivery/cbc:ActualDeliveryDate'),
        notes=notes,
        supplier_vat_id=supplier_vat_id,
        supplier_country_code=country,
        supplier_street=street,
        supplier_city=city,
        supplier_postal_code=postal,
        line_extension_amount=_optional_decimal(
            _text(root, '//cac:LegalMonetaryTotal/cbc:LineExtensionAmount', default='') or None
        ),
        charge_total_amount=_optional_decimal(
            _text(root, '//cac:LegalMonetaryTotal/cbc:ChargeTotalAmount', default='') or None
        ),
        allowance_total_amount=_optional_decimal(
            _text(root, '//cac:LegalMonetaryTotal/cbc:AllowanceTotalAmount', default='') or None
        ),
        lines=lines,
        charges=_parse_charges(root),
        tax_summary=_parse_tax_summary(root),
        references=_parse_references(root),
    )
