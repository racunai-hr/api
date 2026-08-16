from __future__ import annotations

import re
from collections import defaultdict
from datetime import time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone

from ubl.domain.document import Delivery, PaymentMeans, UblDocument
from ubl.domain.line import InvoiceLine
from ubl.domain.party import Address, Contact, Party, SellerContact
from ubl.domain.totals import MonetaryTotal, TaxSubtotal
from ubl.hrcius.constants import ENDPOINT_SCHEME_ID, hr_vat_name


def _clean_oib(value: str) -> str:
    return (value or '').replace('HR', '').strip()


def _party_identification(oib: str, suffix: str = '12345') -> str:
    return f'{ENDPOINT_SCHEME_ID}:{oib}::HR99:{suffix}'


def _resolve_issue_time(invoice, company) -> time:
    tz_name = getattr(company, 'timezone', None) or 'Europe/Zagreb'
    now = timezone.now().astimezone(ZoneInfo(tz_name))
    if invoice.issue_date == now.date():
        return now.time().replace(microsecond=0)
    return time(12, 0, 0)


def _resolve_operator(invoice, company) -> tuple[str, str]:
    operator_name = 'Operater'
    operator_oib = ''

    if invoice.responsible_person:
        operator_name = invoice.responsible_person.full_name
        operator_oib = getattr(invoice.responsible_person, 'oib', '') or ''

    if not operator_oib and hasattr(company, 'operator_oib'):
        operator_oib = company.operator_oib or ''

    if not operator_oib:
        from settings.models import SystemParameter

        param = SystemParameter.all_objects.filter(
            tenant=invoice.tenant,
            key='eracun_operator_oib',
        ).first()
        if param and param.value.strip():
            operator_oib = param.value.strip()

    return operator_name, _clean_oib(operator_oib)


def _company_address_for_ubl(company) -> tuple[str, str, str]:
    street = company.formatted_street or company.company_name
    city = company.city or 'Zagreb'
    postal_code = company.postal_code or '10000'
    if not company.city and company.formatted_city_line:
        match = re.match(r'^(\d{5})\s+(.+)$', company.formatted_city_line)
        if match:
            postal_code, city = match.group(1), match.group(2)
        else:
            city = company.formatted_city_line
    return street, city, postal_code


def invoice_to_ubl_document(invoice, company, partner) -> UblDocument:
    supplier_oib = _clean_oib(company.vat_number or '')
    customer_oib = _clean_oib(partner.tax_number or '')

    supplier_street, supplier_city, supplier_postal = _company_address_for_ubl(company)

    lines: list[InvoiceLine] = []
    tax_buckets: dict[Decimal, Decimal] = defaultdict(Decimal)

    for index, item in enumerate(invoice.items.all(), start=1):
        line_net = (item.quantity or Decimal('0')) * (item.unit_price or Decimal('0'))
        rate = Decimal(str(item.tax_rate or 0))
        tax_buckets[rate] += line_net
        lines.append(
            InvoiceLine(
                line_id=str(index),
                quantity=Decimal(str(item.quantity)),
                line_extension_amount=line_net.quantize(Decimal('0.01')),
                item_name=item.item_name,
                tax_name=hr_vat_name(rate),
                tax_percent=rate,
                unit_price=Decimal(str(item.unit_price)),
                classification_code='62.90.90',
            )
        )

    tax_subtotals = []
    for rate, taxable in sorted(tax_buckets.items(), reverse=True):
        tax_amount = (taxable * rate / Decimal('100')).quantize(Decimal('0.01'))
        tax_subtotals.append(
            TaxSubtotal(
                taxable_amount=taxable.quantize(Decimal('0.01')),
                tax_amount=tax_amount,
                percent=rate,
            )
        )

    operator_name, operator_oib = _resolve_operator(invoice, company)

    seller_contact = None
    if operator_oib:
        seller_contact = SellerContact(
            oib=operator_oib,
            name=operator_name,
        )

    payment_means = None
    iban = getattr(company, 'iban', '') or getattr(company, 'bank_account', '')
    if iban:
        payment_means = PaymentMeans(iban=iban)

    delivery = None
    if invoice.service_date:
        delivery = Delivery(actual_delivery_date=invoice.service_date)

    return UblDocument(
        document_number=invoice.invoice_number,
        issue_date=invoice.issue_date,
        issue_time=_resolve_issue_time(invoice, company),
        due_date=invoice.due_date,
        supplier=Party(
            name=company.company_name,
            oib=supplier_oib,
            address=Address(
                street=supplier_street,
                city=supplier_city,
                postal_zone=supplier_postal,
            ),
            party_identification=_party_identification(supplier_oib),
            contact=Contact(
                name=company.company_name,
                email=company.company_email or '',
            ) if company.company_email else None,
        ),
        customer=Party(
            name=partner.name,
            oib=customer_oib,
            address=Address(
                street=(partner.address or partner.name).split('\n')[0],
                city=partner.city or 'Zagreb',
                postal_zone=partner.postal_code or '10000',
            ),
            party_identification=_party_identification(customer_oib, '98765'),
        ),
        seller_contact=seller_contact,
        delivery=delivery,
        payment_means=payment_means,
        payment_terms_note=invoice.notes or None,
        lines=lines,
        totals=MonetaryTotal(
            line_extension_amount=invoice.subtotal,
            tax_exclusive_amount=invoice.subtotal,
            tax_inclusive_amount=invoice.total_amount,
            payable_amount=invoice.total_amount,
        ),
        tax_subtotals=tax_subtotals,
    )
