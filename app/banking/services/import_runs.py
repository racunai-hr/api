"""Async orchestration for bank statement imports (ADR-0021 Slice 1).

Existing ``import_bank_statement_file`` remains the synchronous core.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from banking.models import BankImportRun, sanitize_original_filename
from banking.parsers.format_detection import detect_format
from banking.services.import_statements import import_bank_statement_file

logger = logging.getLogger(__name__)

SUPPORTED_ASYNC_FORMATS = frozenset({'camt053'})


class IdempotencyConflict(ValidationError):
    """Same Idempotency-Key with a different content hash."""


@dataclass(frozen=True)
class CreateImportOutcome:
    run: BankImportRun
    created: bool
    rejected: bool


def banking_import_max_bytes() -> int:
    return int(getattr(settings, 'BANKING_IMPORT_MAX_BYTES', 20 * 1024 * 1024))


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _as_bytes(content: bytes | str) -> bytes:
    if isinstance(content, bytes):
        return content
    return content.encode('utf-8')


def create_import_run(
    *,
    tenant,
    actor,
    content: bytes | str,
    filename: str = '',
    idempotency_key: str = '',
    source: str = BankImportRun.SOURCE_UPLOAD,
) -> CreateImportOutcome:
    raw = _as_bytes(content)
    max_bytes = banking_import_max_bytes()
    if len(raw) > max_bytes:
        raise ValidationError(f'Datoteka premašuje maksimalnu veličinu ({max_bytes} bajtova).')
    if not raw:
        raise ValidationError('Datoteka je prazna.')

    digest = _sha256_bytes(raw)
    key = (idempotency_key or '').strip()
    safe_name = sanitize_original_filename(filename)

    if key:
        existing = (
            BankImportRun.all_objects.filter(tenant=tenant, actor=actor, idempotency_key=key)
            .order_by('-created_at')
            .first()
        )
        if existing is not None:
            if existing.content_sha256 != digest:
                raise IdempotencyConflict(
                    'Idempotency-Key već postoji s drugačijim sadržajem datoteke.'
                )
            return CreateImportOutcome(run=existing, created=False, rejected=existing.status == BankImportRun.STATUS_REJECTED)

    fmt = detect_format(raw, filename)
    status = (
        BankImportRun.STATUS_QUEUED
        if fmt in SUPPORTED_ASYNC_FORMATS
        else BankImportRun.STATUS_REJECTED
    )

    run = BankImportRun(
        tenant=tenant,
        actor=actor,
        source=source,
        format=fmt,
        original_filename=safe_name,
        content_sha256=digest,
        status=status,
        idempotency_key=key,
    )
    if status == BankImportRun.STATUS_REJECTED:
        run.errors = [f'Nepodržan format: {fmt}']
        run.error_count = 1
        run.finished_at = timezone.now()

    run.payload.save('payload.bin', ContentFile(raw), save=False)
    run.save()
    return CreateImportOutcome(
        run=run,
        created=True,
        rejected=status == BankImportRun.STATUS_REJECTED,
    )


def enqueue_import_run(run_id: int) -> BankImportRun:
    from banking.tasks import execute_bank_import_run_task

    run = BankImportRun.all_objects.select_related('tenant').get(pk=run_id)
    if run.status == BankImportRun.STATUS_REJECTED:
        return run
    if run.status in BankImportRun.TERMINAL_STATUSES:
        return run
    if run.status != BankImportRun.STATUS_QUEUED:
        return run

    async_result = execute_bank_import_run_task.delay(run.pk)
    BankImportRun.all_objects.filter(pk=run.pk, status=BankImportRun.STATUS_QUEUED).update(
        celery_task_id=async_result.id or '',
    )
    run.refresh_from_db()
    return run


def _claim_queued_run(run_id: int) -> BankImportRun | None:
    now = timezone.now()
    with transaction.atomic():
        updated = BankImportRun.all_objects.filter(
            pk=run_id,
            status=BankImportRun.STATUS_QUEUED,
        ).update(status=BankImportRun.STATUS_RUNNING, started_at=now)
        if updated != 1:
            return None
        return BankImportRun.all_objects.select_related('tenant', 'actor').get(pk=run_id)


def _sanitize_error_message(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:500]


def execute_import_run(run_id: int) -> BankImportRun:
    run = _claim_queued_run(run_id)
    if run is None:
        existing = BankImportRun.all_objects.filter(pk=run_id).first()
        if existing is None:
            raise BankImportRun.DoesNotExist(f'BankImportRun #{run_id} ne postoji.')
        return existing

    try:
        run.payload.open('rb')
        try:
            content = run.payload.read()
        finally:
            run.payload.close()

        result = import_bank_statement_file(
            tenant=run.tenant,
            user=run.actor,
            content=content,
            filename=run.original_filename,
        )

        run.statements_processed = result.statements_processed
        run.statements_created = result.statements_created
        run.statements_updated = result.statements_updated
        run.transactions_processed = result.transactions_processed
        run.transactions_created = result.transactions_created
        run.transactions_skipped = result.transactions_skipped
        run.warnings = list(result.warnings)
        run.errors = list(result.errors)
        run.error_count = len(result.errors)
        run.format = result.format or run.format
        run.finished_at = timezone.now()

        if result.errors and not result.statements_created and not result.transactions_created:
            run.status = BankImportRun.STATUS_FAILED
        else:
            run.status = BankImportRun.STATUS_SUCCEEDED

        run.save(
            update_fields=[
                'statements_processed',
                'statements_created',
                'statements_updated',
                'transactions_processed',
                'transactions_created',
                'transactions_skipped',
                'warnings',
                'errors',
                'error_count',
                'format',
                'status',
                'finished_at',
            ],
        )
    except Exception as exc:
        logger.exception('BankImportRun #%s failed', run_id)
        run.status = BankImportRun.STATUS_FAILED
        run.errors = [_sanitize_error_message(exc)]
        run.error_count = 1
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'errors', 'error_count', 'finished_at'])

    return run


def submit_statement_import(
    *,
    tenant,
    actor,
    content: bytes | str,
    filename: str = '',
    idempotency_key: str = '',
) -> CreateImportOutcome:
    """Create run and enqueue when queued; rejected runs are not enqueued."""
    outcome = create_import_run(
        tenant=tenant,
        actor=actor,
        content=content,
        filename=filename,
        idempotency_key=idempotency_key,
    )
    if outcome.run.status == BankImportRun.STATUS_QUEUED and outcome.created:
        enqueue_import_run(outcome.run.pk)
        outcome.run.refresh_from_db()
    elif outcome.run.status == BankImportRun.STATUS_QUEUED and not outcome.created:
        # Replay of in-flight/queued run — do not enqueue again.
        pass
    return outcome
