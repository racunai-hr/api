from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from lxml import etree

from expenses.services.partner_resolver import resolve_partner
from invoices.models import Invoice, InvoiceItem
from ubl.parser.invoice import NS, ParsedInvoice, parse_invoice_ubl


def _system_user():
    User = get_user_model()
    return User.objects.filter(is_superuser=True).first() or User.objects.first()


def _as_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _customer_from_ubl(ubl_xml: str) -> tuple[str, str]:
    root = etree.fromstring(ubl_xml.encode('utf-8'))
    party_nodes = root.xpath('//cac:AccountingCustomerParty/cac:Party', namespaces=NS)
    party = party_nodes[0] if party_nodes else root
    name_el = party.xpath(
        './/cac:PartyLegalEntity/cbc:RegistrationName | .//cac:PartyName/cbc:Name',
        namespaces=NS,
    )
    name = (name_el[0].text or '').strip() if name_el else ''
    oib_el = party.xpath(
        './/cbc:EndpointID | .//cac:PartyTaxScheme/cbc:CompanyID | .//cac:PartyLegalEntity/cbc:CompanyID',
        namespaces=NS,
    )
    oib = ((oib_el[0].text or '').strip() if oib_el else '').replace('HR', '').strip()
    return name, oib


def _qty(value: Decimal | None) -> Decimal:
    qty = value if value is not None and value > 0 else Decimal('1')
    if qty < Decimal('0.01'):
        return Decimal('0.01')
    return qty.quantize(Decimal('0.01'))


def _invoice_status(item: dict) -> str:
    if item.get('payment_status') == 'PAID':
        return 'paid'
    if item.get('recipient_status') == 'REJECTED':
        return 'cancelled'
    return 'sent'


@dataclass(frozen=True)
class OutboundInvoiceDraft:
    invoice: Invoice
    parsed: ParsedInvoice


def create_outbound_invoice_from_ubl(
    *,
    tenant,
    ubl_xml: str,
    gateway_item: dict | None = None,
    fallback_customer_oib: str = '',
    unknown_customer_ref: str = '',
) -> OutboundInvoiceDraft:
    from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

    parsed = parse_invoice_ubl(ubl_xml)
    issue_date = _as_date(parsed.issue_date) or timezone.now().date()
    lock_open_vat_period_for_source_mutation(tenant, issue_date)
    user = _system_user()
    if not user:
        raise ValueError('Nema korisnika za kreiranje računa')

    customer_name, customer_oib = _customer_from_ubl(ubl_xml)
    customer = resolve_partner(
        tenant=tenant,
        oib=customer_oib or fallback_customer_oib or f'unknown-{unknown_customer_ref[:8]}',
        name=customer_name or '',
        partner_type='customer',
    )

    number = (parsed.invoice_number or '')[:50]
    if not number:
        raise ValueError('UBL nema broj računa')
    if Invoice.all_objects.filter(tenant=tenant, invoice_number=number).exists():
        raise ValueError(f'Račun {number} već postoji')

    item = gateway_item if isinstance(gateway_item, dict) else {}
    invoice = Invoice.all_objects.create(
        tenant=tenant,
        invoice_number=number,
        status='draft',
        company_to=customer,
        issue_date=issue_date,
        due_date=_as_date(parsed.due_date) or issue_date,
        subtotal=parsed.subtotal,
        tax_amount=parsed.tax_amount,
        total_amount=parsed.total_amount,
        description='; '.join(parsed.line_descriptions) or f'Izlazni eRačun {number}',
        notes='\n'.join(parsed.notes),
        created_by=user,
    )
    lines = parsed.lines or []
    if not lines:
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name=invoice.description[:200] or number,
            quantity=Decimal('1'),
            unit_price=parsed.subtotal,
            tax_rate=Decimal('0') if parsed.subtotal == 0 else (
                (parsed.tax_amount / parsed.subtotal * 100) if parsed.subtotal else Decimal('0')
            ),
        )
    else:
        for line in lines:
            qty = _qty(line.quantity)
            price = line.unit_price
            if price is None and line.line_extension_amount is not None:
                price = (line.line_extension_amount / qty) if qty else line.line_extension_amount
            InvoiceItem.objects.create(
                invoice=invoice,
                item_name=(line.name or line.description or 'Stavka')[:200],
                description=line.description or '',
                quantity=qty,
                unit_price=price if price is not None else Decimal('0'),
                tax_rate=line.vat_rate if line.vat_rate is not None else Decimal('0'),
            )
    return OutboundInvoiceDraft(invoice=invoice, parsed=parsed)
