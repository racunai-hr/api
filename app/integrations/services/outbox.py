from __future__ import annotations

import hashlib
from datetime import timedelta

from django.utils import timezone

from integrations.models import IntegrationOutboxMessage

RETRY_DELAYS_MINUTES = (1, 5, 15, 60)


def build_idempotency_key(*, tenant, operation: str, invoice_id: int | None, direction: str = 'outbound') -> str:
    raw = f'{tenant.pk}:{operation}:{direction}:{invoice_id or 0}'
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def enqueue_outbox(
    *,
    tenant,
    operation: str,
    provider: str,
    correlation_id,
    invoice=None,
    payload_ref: dict | None = None,
    last_error: str = '',
) -> IntegrationOutboxMessage:
    invoice_id = invoice.pk if invoice is not None else None
    key = build_idempotency_key(tenant=tenant, operation=operation, invoice_id=invoice_id)
    message, created = IntegrationOutboxMessage.all_objects.get_or_create(
        idempotency_key=key,
        defaults={
            'tenant': tenant,
            'operation': operation,
            'provider': provider,
            'correlation_id': correlation_id,
            'invoice': invoice,
            'payload_ref': payload_ref or {},
            'last_error': last_error,
            'next_retry_at': timezone.now(),
        },
    )
    if not created and message.status in (
        IntegrationOutboxMessage.STATUS_DEAD,
        IntegrationOutboxMessage.STATUS_SUCCEEDED,
    ):
        message.status = IntegrationOutboxMessage.STATUS_PENDING
        message.attempt_count = 0
        message.last_error = last_error
        message.next_retry_at = timezone.now()
        message.correlation_id = correlation_id
        message.save(
            update_fields=[
                'status',
                'attempt_count',
                'last_error',
                'next_retry_at',
                'correlation_id',
                'updated_at',
            ],
        )
    elif not created and last_error:
        message.last_error = last_error
        message.save(update_fields=['last_error', 'updated_at'])
    return message


def schedule_next_retry(message: IntegrationOutboxMessage) -> None:
    message.attempt_count += 1
    if message.attempt_count >= message.max_attempts:
        message.status = IntegrationOutboxMessage.STATUS_DEAD
        message.next_retry_at = None
    else:
        delay_idx = min(message.attempt_count - 1, len(RETRY_DELAYS_MINUTES) - 1)
        delay = RETRY_DELAYS_MINUTES[delay_idx]
        message.status = IntegrationOutboxMessage.STATUS_RETRYING
        message.next_retry_at = timezone.now() + timedelta(minutes=delay)
    message.save(
        update_fields=['attempt_count', 'status', 'next_retry_at', 'updated_at'],
    )


def reset_for_reprocess(message: IntegrationOutboxMessage) -> None:
    message.status = IntegrationOutboxMessage.STATUS_PENDING
    message.attempt_count = 0
    message.next_retry_at = timezone.now()
    message.last_error = ''
    message.save(
        update_fields=['status', 'attempt_count', 'next_retry_at', 'last_error', 'updated_at'],
    )
