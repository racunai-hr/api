from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from banking.provider_models import BankConnection, BankConsent
from banking.models import BankSyncRun
from banking.services.alerting import notify_consent_expiry, notify_stale_connection
from banking.services.sync_runs import create_or_get_active_sync_run, execute_sync_run


@shared_task(name='banking.execute_bank_import_run')
def execute_bank_import_run_task(run_id: int) -> dict:
    from banking.services.import_runs import execute_import_run

    run = execute_import_run(run_id)
    return {
        'run_id': run.pk,
        'status': run.status,
        'transactions_created': run.transactions_created,
        'transactions_skipped': run.transactions_skipped,
        'error_count': run.error_count,
    }


@shared_task(name='banking.execute_bank_sync_run')
def execute_bank_sync_run_task(run_id: int) -> dict:
    run = execute_sync_run(run_id)
    return {
        'run_id': run.pk,
        'status': run.status,
        'transactions_created': run.transactions_created,
        'last_error': run.last_error,
    }


@shared_task(name='banking.sync_connection')
def sync_connection_task(
    connection_id: int,
    *,
    auto_reconcile: bool = True,
    auto_create_payments: bool | None = None,
    actor_id: int | None = None,
) -> dict:
    """Enqueue a BankSyncRun (shared lock). Legacy auto_reconcile maps to auto_create_payments."""
    from django.contrib.auth import get_user_model

    connection = BankConnection.all_objects.select_related('tenant', 'bank_provider').get(
        pk=connection_id,
    )
    if auto_create_payments is None:
        auto_create_payments = bool(auto_reconcile)
    actor = None
    if actor_id is not None:
        actor = get_user_model().objects.filter(pk=actor_id).first()

    run, created = create_or_get_active_sync_run(
        connection=connection,
        actor=actor,
        auto_create_payments=auto_create_payments,
    )
    if created and run.status == BankSyncRun.STATUS_QUEUED:
        from banking.services.sync_runs import enqueue_sync_run

        enqueue_sync_run(run.pk)
        run.refresh_from_db()
    elif run.status == BankSyncRun.STATUS_QUEUED:
        # Active queued without task — ensure worker pickup.
        from banking.services.sync_runs import enqueue_sync_run

        if not run.celery_task_id:
            enqueue_sync_run(run.pk)
            run.refresh_from_db()

    return {
        'sync_run_id': run.pk,
        'created': created,
        'status': run.status,
        'transactions_created': run.transactions_created,
    }


@shared_task(name='banking.sync_all_active_connections')
def sync_all_active_connections_task():
    results = []
    for connection in BankConnection.all_objects.filter(status='connected').iterator():
        try:
            result = sync_connection_task(connection.pk, auto_create_payments=True)
            results.append({'connection_id': connection.pk, **result})
        except Exception as exc:
            connection.status = 'error'
            connection.last_error = str(exc)[:500]
            connection.sync_success_streak = 0
            connection.save(
                update_fields=['status', 'last_error', 'sync_success_streak', 'updated_at'],
            )
            results.append({'connection_id': connection.pk, 'error': str(exc)})
    return results


@shared_task(name='banking.check_consent_expiry')
def check_consent_expiry_task():
    today = timezone.localdate()
    warn_14 = today + timedelta(days=14)
    warn_3 = today + timedelta(days=3)
    updated = []

    consents = BankConsent.objects.filter(
        status='authorized',
        valid_until__isnull=False,
    ).select_related('connection', 'connection__tenant')

    for consent in consents.iterator():
        if consent.valid_until < today:
            level = 'expired'
            consent.status = 'expired'
            consent.expiry_warning_level = level
            consent.expiry_notified_at = timezone.now()
            consent.save(update_fields=['status', 'expiry_warning_level', 'expiry_notified_at', 'updated_at'])
            connection = consent.connection
            connection.status = 'expired'
            connection.sync_success_streak = 0
            connection.save(update_fields=['status', 'sync_success_streak', 'updated_at'])
            notify_consent_expiry(
                tenant_slug=connection.tenant.slug,
                consent_id=consent.consent_id or str(consent.pk),
                level=level,
                valid_until=consent.valid_until,
            )
            updated.append(consent.pk)
            continue

        if consent.valid_until <= warn_3 and consent.expiry_warning_level not in ('3d', 'expired'):
            level = '3d'
        elif consent.valid_until <= warn_14 and consent.expiry_warning_level == 'none':
            level = '14d'
        else:
            continue

        consent.expiry_warning_level = level
        consent.expiry_notified_at = timezone.now()
        consent.save(update_fields=['expiry_warning_level', 'expiry_notified_at', 'updated_at'])
        notify_consent_expiry(
            tenant_slug=consent.connection.tenant.slug,
            consent_id=consent.consent_id or str(consent.pk),
            level=level,
            valid_until=consent.valid_until,
        )
        updated.append(consent.pk)

    return {'notified_consents': updated}


@shared_task(name='banking.check_stale_connections')
def check_stale_connections_task():
    cutoff = timezone.now() - timedelta(hours=24)
    stale = BankConnection.all_objects.filter(
        status='connected',
        last_sync_at__lt=cutoff,
    ).select_related('tenant')

    notified = []
    for connection in stale.iterator():
        notify_stale_connection(
            connection_id=connection.pk,
            tenant_slug=connection.tenant.slug,
            last_sync_at=connection.last_sync_at,
        )
        notified.append(connection.pk)

    return {'stale_connections': notified}
