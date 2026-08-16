from __future__ import annotations

from decimal import Decimal

from fiscal_gateway.signing.builders import (
    FiscalDocumentTotals,
    FiscalIssuer,
    FiscalLineItem,
    FiscalParty,
    FiscalVatBreakdown,
    OutgoingInvoicePayload,
    format_decimal,
)
from ubl.domain.document import UblDocument


def ubl_document_to_fiscal_payload(document: UblDocument) -> OutgoingInvoicePayload:
    issuer = FiscalIssuer(
        name=document.supplier.name,
        oib=document.supplier.oib.replace('HR', ''),
        operator_oib=document.seller_contact.oib if document.seller_contact else document.supplier.oib.replace('HR', ''),
    )
    recipient = FiscalParty(
        name=document.customer.name,
        oib=document.customer.oib.replace('HR', ''),
    )

    line_items = []
    for line in document.lines:
        line_items.append(
            FiscalLineItem(
                quantity=format_decimal(line.quantity, 3),
                unit=line.unit_code,
                line_net=format_decimal(line.line_extension_amount),
                net_price=format_decimal(line.unit_price, 6),
                base_quantity=format_decimal(line.base_quantity, 3),
                base_unit=line.unit_code,
                vat_category=line.tax_category,
                vat_rate=format_decimal(line.tax_percent, 0).split('.')[0],
                name=line.item_name,
                classification_id=line.classification_code or '62.90.90',
                classification_scheme=line.classification_scheme,
            )
        )

    primary_vat = document.tax_subtotals[0] if document.tax_subtotals else None
    vat_breakdown = FiscalVatBreakdown(
        category=primary_vat.category_id if primary_vat else 'S',
        taxable_amount=format_decimal(primary_vat.taxable_amount) if primary_vat else '0.00',
        tax_amount=format_decimal(primary_vat.tax_amount) if primary_vat else '0.00',
        rate=format_decimal(primary_vat.percent, 0).split('.')[0] if primary_vat else '25',
    )

    totals = FiscalDocumentTotals(
        net=format_decimal(document.totals.line_extension_amount),
        amount_without_vat=format_decimal(document.totals.tax_exclusive_amount),
        vat=format_decimal(
            sum((s.tax_amount for s in document.tax_subtotals), Decimal('0'))
        ),
        amount_with_vat=format_decimal(document.totals.tax_inclusive_amount),
        payable=format_decimal(document.totals.payable_amount),
    )

    return OutgoingInvoicePayload(
        document_number=document.document_number,
        issue_date=document.issue_date.isoformat(),
        due_date=document.due_date.isoformat() if document.due_date else '',
        document_type=document.invoice_type_code,
        currency=document.currency,
        business_process=document.profile_id,
        payment_account=document.payment_means.iban if document.payment_means else '',
        issuer=issuer,
        recipient=recipient,
        totals=totals,
        vat_breakdown=vat_breakdown,
        line_items=line_items,
    )
