from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounting.services.posting import post_invoice_payment
from banking.provider_models import PaymentOrder
from events.payment import PaymentExecuted

logger = logging.getLogger(__name__)


def _get_system_user():
    User = get_user_model()
    return User.objects.filter(is_superuser=True).first()


@transaction.atomic
def handle_payment_executed(event: PaymentExecuted):
    try:
        order = PaymentOrder.all_objects.select_related(
            'payment',
            'payment__related_invoice',
        ).get(pk=event.payment_order_id)
    except PaymentOrder.DoesNotExist:
        logger.warning('PaymentExecuted: PaymentOrder #%s ne postoji.', event.payment_order_id)
        return None

    if order.posting_journal_entry_id:
        return order.posting_journal_entry

    if order.payment_id is None:
        logger.info(
            'PaymentExecuted: PaymentOrder #%s nema povezanog Payment zapisa — preskačem knjiženje.',
            order.pk,
        )
        return None

    payment = order.payment
    if payment.status != 'completed':
        payment.status = 'completed'
        payment.save(update_fields=['status', 'updated_at'])

    invoice = payment.related_invoice
    if invoice is not None and invoice.status != 'paid':
        invoice.status = 'paid'
        invoice.save(update_fields=['status', 'updated_at'])

    user = _get_system_user()
    if user is None:
        logger.warning('PaymentExecuted: nema system usera za knjiženje (order #%s).', order.pk)
        return None

    if invoice is None:
        logger.info(
            'PaymentExecuted: Payment #%s nema povezanog računa — preskačem knjiženje.',
            payment.pk,
        )
        return None

    entry = post_invoice_payment(order.tenant, invoice, payment, user)
    if entry is None:
        return None

    order.posting_journal_entry = entry
    order.posted_at = timezone.now()
    order.save(update_fields=['posting_journal_entry', 'posted_at', 'updated_at'])
    return entry
