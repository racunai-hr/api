"""Jedino mjesto za promjenu PaymentExecution.status — ne postavljati execution.status izvan ovog modula."""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Max

from banking.provider_models import PaymentExecution, PaymentExecutionTransition
from banking.services.payment_execution_status_guard import lifecycle_status_change
from banking.services.payment_order_lifecycle import PaymentOrderLifecycle

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
    'rejected': frozenset(),
    'failed': frozenset(),
}


class InvalidPaymentExecutionTransition(Exception):
    def __init__(self, execution_id: int, from_status: str, to_status: str) -> None:
        self.execution_id = execution_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f'PaymentExecution #{execution_id}: nedozvoljen prijelaz {from_status!r} → {to_status!r}',
        )


class ExecutionLifecycle:
    @staticmethod
    @transaction.atomic
    def transition(
        execution: PaymentExecution,
        new_status: str,
        *,
        actor: str,
        reason: str = '',
        metadata: dict | None = None,
    ) -> PaymentExecution:
        if new_status == execution.status:
            return execution

        from_status = execution.status
        allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
        if new_status not in allowed:
            raise InvalidPaymentExecutionTransition(execution.pk, from_status, new_status)

        with lifecycle_status_change():
            execution.status = new_status
            execution.save(update_fields=['status', 'updated_at'])

        next_sequence = (
            PaymentExecutionTransition.objects.filter(execution_id=execution.pk)
            .aggregate(max_seq=Max('sequence'))
            .get('max_seq')
            or 0
        ) + 1

        PaymentExecutionTransition.objects.create(
            tenant=execution.tenant,
            execution=execution,
            sequence=next_sequence,
            from_status=from_status,
            to_status=new_status,
            actor=actor,
            reason=reason,
            correlation_id=execution.correlation_id,
            metadata=metadata or {},
        )

        logger.info(
            'PaymentExecution %s (order=%s, attempt=%s): %s → %s (actor=%s, correlation_id=%s)',
            execution.pk,
            execution.order_id,
            execution.attempt,
            from_status,
            new_status,
            actor,
            execution.correlation_id,
        )

        PaymentOrderLifecycle.transition(
            execution.order,
            new_status,
            actor=actor,
            reason=reason,
            metadata=metadata,
        )

        return execution
