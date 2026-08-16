from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from integrations.manager import IntegrationManager
from integrations.models import IntegrationOutboxMessage
from integrations.retry_policy import is_retryable_exception
from integrations.services.outbox import schedule_next_retry

logger = logging.getLogger(__name__)


def _process_send_eracun(message: IntegrationOutboxMessage) -> None:
    invoice = message.invoice
    if invoice is None:
        raise ValueError('Outbox poruka nema invoice FK')

    IntegrationManager.send_eracun(
        invoice,
        triggered_by=None,
        skip_outbox_enqueue=True,
    )


@shared_task(name='fiscal_gateway.process_outbox_message')
def process_outbox_message_task(outbox_id: int) -> str:
    message = IntegrationOutboxMessage.all_objects.select_related('invoice', 'tenant').get(pk=outbox_id)
    if message.status == IntegrationOutboxMessage.STATUS_SUCCEEDED:
        return 'already_succeeded'

    if message.status == IntegrationOutboxMessage.STATUS_DEAD:
        return 'dead'

    try:
        if message.operation == IntegrationOutboxMessage.OPERATION_SEND_ERACUN:
            _process_send_eracun(message)
        else:
            raise ValueError(f'Nepodržana operacija: {message.operation}')

        message.status = IntegrationOutboxMessage.STATUS_SUCCEEDED
        message.next_retry_at = None
        message.save(update_fields=['status', 'next_retry_at', 'updated_at'])
        return 'succeeded'
    except Exception as exc:
        message.last_error = str(exc)
        if is_retryable_exception(exc):
            schedule_next_retry(message)
            if message.status == IntegrationOutboxMessage.STATUS_DEAD:
                from integrations.audit import log_audit_step
                from integrations.models import IntegrationAuditLog

                log_audit_step(
                    tenant=message.tenant,
                    step=IntegrationAuditLog.STEP_OUTBOUND_FAILED,
                    status=IntegrationAuditLog.STATUS_FAILED,
                    correlation_id=message.correlation_id,
                    invoice=message.invoice,
                    integration_type='eracun',
                    provider=message.provider,
                    detail={'messages': [str(exc)], 'dead_letter': True},
                )
            return f'retry_scheduled:{message.attempt_count}'
        message.status = IntegrationOutboxMessage.STATUS_DEAD
        message.next_retry_at = None
        message.save(update_fields=['status', 'last_error', 'next_retry_at', 'updated_at'])
        return f'terminal:{exc}'


@shared_task(name='fiscal_gateway.process_due_outbox_messages')
def process_due_outbox_messages_task() -> int:
    now = timezone.now()
    due = IntegrationOutboxMessage.all_objects.filter(
        status__in=(
            IntegrationOutboxMessage.STATUS_PENDING,
            IntegrationOutboxMessage.STATUS_RETRYING,
        ),
        next_retry_at__lte=now,
    ).values_list('pk', flat=True)[:50]

    count = 0
    for pk in due:
        process_outbox_message_task.delay(pk)
        count += 1
    return count
