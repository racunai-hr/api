"""Banking write orchestration for JWT API (ADR-0021 Slice 4)."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404

from accounting.models import JournalEntry
from banking.models import BankTransaction
from banking.provider_models import BankConnection
from banking.reconciliation import (
    MatchConflict,
    match_transaction,
    match_transaction_to_journal_entry,
    unmatch_transaction,
)
from banking.services.import_runs import (
    IdempotencyConflict,
    create_import_run,
    enqueue_import_run,
)
from banking.services.sync_runs import create_or_get_active_sync_run, enqueue_sync_run
from banking.models import BankSyncRun
from domains.banking.read.dto import import_run_dto, sync_run_dto, transaction_dto
from payments.models import Payment


@dataclass(frozen=True)
class ImportSubmitResult:
    payload: dict
    status_code: int
    created: bool


def _validation_detail(exc: ValidationError) -> str | list | dict:
    if hasattr(exc, 'message_dict') and exc.message_dict:
        return exc.message_dict
    if hasattr(exc, 'messages'):
        messages = list(exc.messages)
        return messages[0] if len(messages) == 1 else messages
    return str(exc)


def submit_import(
    *,
    tenant,
    actor,
    content: bytes,
    filename: str,
    idempotency_key: str,
) -> ImportSubmitResult:
    key = (idempotency_key or '').strip()
    if not key:
        raise ValidationError({'Idempotency-Key': 'Header Idempotency-Key je obavezan.'})
    if not content:
        raise ValidationError({'file': 'Datoteka je prazna.'})

    try:
        outcome = create_import_run(
            tenant=tenant,
            actor=actor,
            content=content,
            filename=filename,
            idempotency_key=key,
        )
    except IdempotencyConflict as exc:
        raise MatchConflict(_validation_detail(exc)) from exc

    run = outcome.run
    if run.status == run.STATUS_QUEUED and outcome.created:
        enqueue_import_run(run.pk)
        run.refresh_from_db()
    elif run.status == run.STATUS_QUEUED and not outcome.created and not run.celery_task_id:
        enqueue_import_run(run.pk)
        run.refresh_from_db()

    payload = import_run_dto(run)
    payload['created'] = outcome.created
    if outcome.rejected or run.status == run.STATUS_REJECTED:
        return ImportSubmitResult(payload=payload, status_code=422, created=outcome.created)
    return ImportSubmitResult(payload=payload, status_code=202, created=outcome.created)


def match_transaction_api(
    *,
    tenant,
    user,
    transaction_id: int,
    target_type: str,
    target_id: int,
) -> dict:
    bank_tx = BankTransaction.all_objects.filter(tenant=tenant, pk=transaction_id).first()
    if bank_tx is None:
        raise Http404()

    kind = (target_type or '').strip()
    if kind not in ('payment', 'journal_entry'):
        raise ValidationError({'target_type': 'target_type mora biti payment ili journal_entry.'})
    if not target_id:
        raise ValidationError({'target_id': 'target_id je obavezan.'})

    try:
        if kind == 'payment':
            payment = Payment.all_objects.filter(tenant=tenant, pk=target_id).first()
            if payment is None:
                raise Http404()
            match_transaction(bank_tx, payment)
        else:
            entry = JournalEntry.all_objects.filter(tenant=tenant, pk=target_id).first()
            if entry is None:
                raise Http404()
            match_transaction_to_journal_entry(bank_tx, entry, user)
    except MatchConflict:
        raise
    except IntegrityError as exc:
        raise MatchConflict('Cilj usklađivanja je već zauzet.') from exc

    bank_tx.refresh_from_db()
    return transaction_dto(bank_tx)


def unmatch_transaction_api(*, tenant, user, transaction_id: int) -> dict:
    bank_tx = BankTransaction.all_objects.filter(tenant=tenant, pk=transaction_id).first()
    if bank_tx is None:
        raise Http404()
    unmatch_transaction(bank_tx, user)
    bank_tx.refresh_from_db()
    return transaction_dto(bank_tx)


def start_sync_api(*, tenant, user, connection_id: int) -> dict:
    connection = BankConnection.all_objects.filter(tenant=tenant, pk=connection_id).first()
    if connection is None:
        raise Http404()

    run, created = create_or_get_active_sync_run(
        connection=connection,
        actor=user,
        auto_create_payments=False,
    )
    if run.status == BankSyncRun.STATUS_QUEUED and (created or not run.celery_task_id):
        enqueue_sync_run(run.pk)
        run.refresh_from_db()

    payload = sync_run_dto(run)
    payload['created'] = created
    payload['auto_create_payments'] = False
    return payload
