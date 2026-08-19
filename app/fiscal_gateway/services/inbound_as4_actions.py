from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from lxml import etree

from expenses.models import Expense
from fiscal_gateway.client.as4_client import As4SendError, submit_application_response_via_domibus
from fiscal_gateway.client.eracun_lookup import INVOICE_NS, UBL_NS
from fiscal_gateway.models import As4DocumentLink
from fiscal_gateway.services.application_response import (
    build_application_response_xml,
    wrap_application_response_sbdh,
)


class InboundExpenseError(Exception):
    pass


@transaction.atomic
def approve_inbound_expense(expense: Expense, user=None) -> As4DocumentLink:
    from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

    lock_open_vat_period_for_source_mutation(expense.tenant, expense.expense_date)
    return _send_manual_application_response(expense, accepted=True, user=user)


@transaction.atomic
def reject_inbound_expense(expense: Expense, description: str = '') -> As4DocumentLink:
    return _send_manual_application_response(
        expense,
        accepted=False,
        reason=description or 'Odbijeno',
    )


def _send_manual_application_response(
    expense: Expense,
    *,
    accepted: bool,
    reason: str = '',
    user=None,
) -> As4DocumentLink:
    link = As4DocumentLink.all_objects.filter(
        tenant=expense.tenant,
        direction=As4DocumentLink.DIRECTION_INBOUND,
        content_type=ContentType.objects.get_for_model(Expense),
        object_id=expense.pk,
    ).first()
    if not link:
        raise InboundExpenseError('Trošak nije povezan s AS4 ulaznim računom')

    if not link.ubl_xml:
        raise InboundExpenseError('Nedostaje UBL XML za AS4 odgovor')

    invoice_root = _invoice_root_from_xml(link.ubl_xml)
    receiver_oib = link.recipient_oib
    supplier_oib = link.supplier_oib

    app_response = build_application_response_xml(
        invoice_root=invoice_root,
        receiver_oib=receiver_oib,
        accepted=accepted,
        reason=reason,
    )
    payload = wrap_application_response_sbdh(
        app_response.xml,
        sender_oib=receiver_oib,
        receiver_oib=supplier_oib,
        response_id=app_response.response_id,
    )

    if not link.from_party_id:
        raise InboundExpenseError('Nedostaje From party ID ulazne AS4 poruke')

    send_result = submit_application_response_via_domibus(
        payload,
        to_ap_party_id=link.from_party_id,
        ref_to_message_id=link.message_id,
        conversation_id=link.conversation_id,
        supplier_oib=supplier_oib,
        receiver_oib=receiver_oib,
    )

    link.as4_status = (
        As4DocumentLink.STATUS_ACCEPTED if accepted else As4DocumentLink.STATUS_REJECTED
    )
    link.ref_message_id = send_result.message_id
    link.save(update_fields=['as4_status', 'ref_message_id', 'last_synced_at'])

    expense.status = 'approved' if accepted else 'rejected'
    update_fields = ['status', 'updated_at']
    if accepted and user is not None:
        expense.approved_by = user
        update_fields.append('approved_by')
    expense.save(update_fields=update_fields)
    return link


def _invoice_root_from_xml(ubl_xml: str) -> etree._Element:
    root = etree.fromstring(ubl_xml.encode('utf-8'))
    invoice = root.find(f'.//{{{INVOICE_NS}}}Invoice')
    if invoice is None and root.tag == f'{{{INVOICE_NS}}}Invoice':
        invoice = root
    if invoice is None:
        raise InboundExpenseError('UBL XML ne sadrži Invoice')
    return invoice
