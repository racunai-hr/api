"""BankSyncRun orchestration — one active AIS sync per connection (ADR-0021)."""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from banking.models import BankSyncRun
from banking.provider_models import BankConnection
from banking.services.sync import sync_and_reconcile, sync_connection_transactions

logger = logging.getLogger(__name__)


def get_active_sync_run(connection: BankConnection) -> BankSyncRun | None:
    return (
        BankSyncRun.all_objects.filter(
            connection=connection,
            status__in=BankSyncRun.ACTIVE_STATUSES,
        )
        .order_by('-created_at')
        .first()
    )


def create_or_get_active_sync_run(
    *,
    connection: BankConnection,
    actor=None,
    auto_create_payments: bool = False,
) -> tuple[BankSyncRun, bool]:
    """Return (run, created). Concurrent creates reuse the active run via unique constraint."""
    existing = get_active_sync_run(connection)
    if existing is not None:
        return existing, False

    try:
        with transaction.atomic():
            run = BankSyncRun.all_objects.create(
                tenant=connection.tenant,
                connection=connection,
                actor=actor,
                status=BankSyncRun.STATUS_QUEUED,
                auto_create_payments=auto_create_payments,
            )
            return run, True
    except IntegrityError:
        existing = get_active_sync_run(connection)
        if existing is None:
            raise
        return existing, False


def enqueue_sync_run(run_id: int) -> BankSyncRun:
    from banking.tasks import execute_bank_sync_run_task

    run = BankSyncRun.all_objects.select_related('connection', 'tenant').get(pk=run_id)
    if run.status != BankSyncRun.STATUS_QUEUED:
        return run
    async_result = execute_bank_sync_run_task.delay(run.pk)
    BankSyncRun.all_objects.filter(pk=run.pk, status=BankSyncRun.STATUS_QUEUED).update(
        celery_task_id=async_result.id or '',
    )
    run.refresh_from_db()
    return run


def _claim_queued_sync(run_id: int) -> BankSyncRun | None:
    now = timezone.now()
    with transaction.atomic():
        updated = BankSyncRun.all_objects.filter(
            pk=run_id,
            status=BankSyncRun.STATUS_QUEUED,
        ).update(status=BankSyncRun.STATUS_RUNNING, started_at=now)
        if updated != 1:
            return None
        return BankSyncRun.all_objects.select_related(
            'connection',
            'connection__bank_account',
            'connection__bank_provider',
            'tenant',
            'actor',
        ).get(pk=run_id)


def execute_sync_run(run_id: int) -> BankSyncRun:
    run = _claim_queued_sync(run_id)
    if run is None:
        existing = BankSyncRun.all_objects.filter(pk=run_id).first()
        if existing is None:
            raise BankSyncRun.DoesNotExist(f'BankSyncRun #{run_id} ne postoji.')
        return existing

    try:
        connection = run.connection
        if run.auto_create_payments:
            result = sync_and_reconcile(connection, auto_create_payments=True)
            created = int(result.get('transactions_synced') or 0)
        else:
            created = sync_connection_transactions(connection)
            result = {'transactions_synced': created}

        # Skipped is not returned by sync today; leave 0 until sync reports it.
        run.transactions_created = created
        run.transactions_skipped = 0
        run.status = BankSyncRun.STATUS_SUCCEEDED
        run.finished_at = timezone.now()
        run.last_error = ''
        run.save(
            update_fields=[
                'transactions_created',
                'transactions_skipped',
                'status',
                'finished_at',
                'last_error',
            ],
        )
        logger.info(
            'BankSyncRun #%s succeeded connection=%s created=%s',
            run.pk,
            connection.pk,
            created,
            extra={'sync_result': result},
        )
    except Exception as exc:
        logger.exception('BankSyncRun #%s failed', run_id)
        run.status = BankSyncRun.STATUS_FAILED
        run.last_error = (str(exc).strip() or exc.__class__.__name__)[:500]
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'last_error', 'finished_at'])

    return run


def start_connection_sync(
    *,
    connection: BankConnection,
    actor=None,
    auto_create_payments: bool = False,
) -> tuple[BankSyncRun, bool]:
    """Create or reuse active sync and enqueue when newly created."""
    run, created = create_or_get_active_sync_run(
        connection=connection,
        actor=actor,
        auto_create_payments=auto_create_payments,
    )
    if created and run.status == BankSyncRun.STATUS_QUEUED:
        enqueue_sync_run(run.pk)
        run.refresh_from_db()
    return run, created
