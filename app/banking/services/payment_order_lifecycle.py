"""Jedino mjesto za promjenu PaymentOrder.status — ne postavljati order.status izvan ovog modula."""

from __future__ import annotations

import logging
from datetime import date

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from banking.provider_models import PaymentOrder, PaymentOrderTransition
from banking.services.payment_order_status_guard import lifecycle_status_change
from events.dispatcher import publish
from events.payment import PaymentExecuted

logger = logging.getLogger(__name__)

ACTOR_SUBMIT = 'submit'
ACTOR_OTP_CALLBACK = 'otp_callback'
ACTOR_OTP_POLLING = 'otp_polling'
ACTOR_ADMIN_ACTION = 'admin_action'
ACTOR_SYSTEM = 'system'
ACTOR_CELERY = 'celery'

TERMINAL_STATUSES = frozenset({'executed', 'rejected', 'failed'})

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    'draft': frozenset({'submitted', 'sca_required', 'failed'}),
    'submitted': frozenset({'sca_required', 'authorised', 'accepted', 'rejected', 'failed'}),
    'sca_required': frozenset({'authorised', 'rejected', 'failed'}),
    'authorised': frozenset({'accepted', 'executed', 'rejected'}),
    'accepted': frozenset({'executed', 'rejected'}),
    'executed': frozenset(),
    'rejected': frozenset({'submitted'}),
    'failed': frozenset({'submitted'}),
}


class InvalidPaymentOrderTransition(Exception):
    def __init__(self, order_id: int, from_status: str, to_status: str) -> None:
        self.order_id = order_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f'PaymentOrder #{order_id}: nedozvoljen prijelaz {from_status!r} → {to_status!r}',
        )


class PaymentOrderLifecycle:
    @staticmethod
    @transaction.atomic
    def transition(
        order: PaymentOrder,
        new_status: str,
        *,
        actor: str,
        reason: str = '',
        metadata: dict | None = None,
    ) -> PaymentOrder:
        if new_status == order.status:
            return order

        from_status = order.status
        allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
        if new_status not in allowed:
            raise InvalidPaymentOrderTransition(order.pk, from_status, new_status)

        with lifecycle_status_change():
            order.status = new_status
            order.save(update_fields=['status', 'updated_at'])

        next_sequence = (
            PaymentOrderTransition.objects.filter(order_id=order.pk)
            .aggregate(max_seq=Max('sequence'))
            .get('max_seq')
            or 0
        ) + 1

        PaymentOrderTransition.objects.create(
            tenant=order.tenant,
            order=order,
            sequence=next_sequence,
            from_status=from_status,
            to_status=new_status,
            actor=actor,
            reason=reason,
            correlation_id=order.correlation_id,
            metadata=metadata or {},
        )

        logger.info(
            'PaymentOrder %s: %s → %s (actor=%s, correlation_id=%s)',
            order.pk,
            from_status,
            new_status,
            actor,
            order.correlation_id,
        )

        if PaymentOrderLifecycle._should_emit_payment_executed(order, from_status, new_status):
            publish(PaymentOrderLifecycle._build_payment_executed_event(order))

        return order

    @staticmethod
    def _should_emit_payment_executed(order: PaymentOrder, from_status: str, to_status: str) -> bool:
        if to_status == 'executed' and from_status != 'executed':
            return True

        if (
            getattr(settings, 'PIS_SANDBOX_ALLOW_POSTING_ON_AUTHORISED', False)
            and order.connection.bank_provider.environment == 'sandbox'
            and to_status == 'authorised'
            and from_status != 'authorised'
        ):
            return True

        return False

    @staticmethod
    def _build_payment_executed_event(order: PaymentOrder) -> PaymentExecuted:
        return PaymentExecuted(
            tenant_id=order.tenant_id,
            payment_order_id=order.pk,
            payment_id=order.payment_id,
            bank_connection_id=order.connection_id,
            provider_code=order.connection.bank_provider.code,
            amount=order.amount,
            currency=order.currency,
            execution_date=date.today(),
            bank_reference=order.otp_payment_id,
            correlation_id=order.correlation_id,
        )
