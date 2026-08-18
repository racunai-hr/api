"""Atomic VAT projection apply. Gate C — not wired to admin/commands."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import AbstractBaseUser
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from accounting.models import (
    JournalEntry,
    JournalEntryLine,
    VATLedgerEntry,
    VATLedgerOrigin,
    VATPeriod,
    VATProjectionRun,
    VATProjectionRunStatus,
)
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING, PDV_MAPPING_VERSION
from accounting.services.tax_projection.contracts import (
    PROJECTION_ENGINE_VERSION,
    ProjectedLedgerRow,
    VatProjectionCandidate,
    VatProjectionStatus,
)
from accounting.services.tax_projection.fingerprints import (
    engine_effect_fingerprint_from_ledger,
    engine_effect_fingerprint_from_rows,
    output_fingerprint,
    resolved_source_object_id,
    writable_rows,
)
from accounting.services.tax_projection.identity import identity_of
from accounting.services.tax_projection.snapshot import (
    documents_from_snapshot,
    load_snapshot,
    snapshot_fingerprint,
)
from accounting.services.tax_shadow.selection import (
    select_expenses,
    select_invoices,
    select_posted_journal_lines,
    select_reversal_entries,
)


class PostWriteFingerprintMismatch(RuntimeError):
    """Raised inside the apply transaction to force full rollback."""


def apply_vat_projection(
    period: VATPeriod,
    candidate: VatProjectionCandidate,
    actor: AbstractBaseUser | None = None,
) -> VATProjectionRun:
    """Apply a READY candidate. Authoritative write gate is locked period.status == open."""
    try:
        with transaction.atomic():
            return _apply_in_transaction(period, candidate, actor)
    except Exception as exc:
        if isinstance(exc, PostWriteFingerprintMismatch):
            _record_failed_run(period, candidate, actor, code='POST_WRITE_FINGERPRINT_MISMATCH')
            raise
        _record_failed_run(period, candidate, actor, code='APPLY_FAILED')
        raise


def _apply_in_transaction(
    period: VATPeriod,
    candidate: VatProjectionCandidate,
    actor: AbstractBaseUser | None,
) -> VATProjectionRun:
    locked_period = (
        VATPeriod.all_objects.select_for_update()
        .select_related('tenant')
        .get(pk=period.pk)
    )

    if locked_period.status != 'open':
        return _audit_rejection(
            locked_period,
            candidate,
            actor,
            status=VATProjectionRunStatus.REJECTED,
            code='VAT_PERIOD_NOT_WRITABLE',
        )

    _lock_sources(locked_period)
    locked_ledger = list(
        VATLedgerEntry.all_objects.filter(vat_period=locked_period)
        .select_for_update()
        .order_by('pk')
    )

    snapshot = load_snapshot(locked_period)
    documents = documents_from_snapshot(snapshot)
    locked_input_fp = snapshot_fingerprint(snapshot, documents)

    rejection = _validate_candidate(locked_period, candidate, locked_input_fp)
    if rejection == 'STALE_PROJECTION':
        return _audit_rejection(
            locked_period,
            candidate,
            actor,
            status=VATProjectionRunStatus.STALE,
            code='STALE_PROJECTION',
        )
    if rejection:
        return _audit_rejection(
            locked_period,
            candidate,
            actor,
            status=VATProjectionRunStatus.REJECTED,
            code=rejection,
        )

    existing = _idempotent_existing_run(locked_period, candidate, locked_ledger)
    if existing is not None:
        return existing

    run = VATProjectionRun.all_objects.create(
        tenant_id=locked_period.tenant_id,
        vat_period=locked_period,
        status=VATProjectionRunStatus.PREPARED,
        engine_version=candidate.engine_version,
        mapping_version=candidate.mapping_version,
        input_fingerprint=candidate.input_fingerprint,
        output_fingerprint=candidate.output_fingerprint,
        classified_count=candidate.classified_count,
        not_relevant_count=candidate.not_relevant_count,
        review_count=candidate.review_count,
        invalid_count=candidate.invalid_count,
        actor=actor if getattr(actor, 'pk', None) else None,
        rejection_code='',
    )

    VATLedgerEntry.all_objects.filter(
        vat_period=locked_period,
        origin=VATLedgerOrigin.ENGINE,
    ).delete()

    _insert_engine_rows(locked_period, candidate, run)

    engine_entries = list(
        VATLedgerEntry.all_objects.filter(
            vat_period=locked_period,
            origin=VATLedgerOrigin.ENGINE,
        )
    )
    expected_fp = engine_effect_fingerprint_from_rows(candidate.rows)
    actual_fp = engine_effect_fingerprint_from_ledger(engine_entries)
    if expected_fp != actual_fp:
        raise PostWriteFingerprintMismatch('POST_WRITE_FINGERPRINT_MISMATCH')

    run.status = VATProjectionRunStatus.APPLIED
    run.applied_at = timezone.now()
    run.save(update_fields=['status', 'applied_at'])
    return run


def _lock_sources(period: VATPeriod) -> None:
    from expenses.models import Expense
    from invoices.models import Invoice, InvoiceItem

    invoices = select_invoices(period)
    invoice_ids = [invoice.pk for invoice in invoices]
    if invoice_ids:
        list(Invoice.all_objects.filter(pk__in=invoice_ids).order_by('pk').select_for_update())
        list(
            InvoiceItem.objects.filter(invoice_id__in=invoice_ids)
            .order_by('pk')
            .select_for_update()
        )

    expenses = select_expenses(period)
    expense_ids = [expense.pk for expense in expenses]
    if expense_ids:
        list(Expense.all_objects.filter(pk__in=expense_ids).order_by('pk').select_for_update())

    lines = select_posted_journal_lines(period)
    line_ids = [line.pk for line in lines]
    if line_ids:
        list(
            JournalEntryLine.objects.filter(pk__in=line_ids)
            .order_by('pk')
            .select_for_update()
        )
        entry_ids = sorted({line.journal_entry_id for line in lines})
        list(JournalEntry.all_objects.filter(pk__in=entry_ids).order_by('pk').select_for_update())

    reversals = select_reversal_entries(period)
    reversal_ids = [entry.pk for entry in reversals]
    if reversal_ids:
        list(
            JournalEntry.all_objects.filter(pk__in=reversal_ids)
            .order_by('pk')
            .select_for_update()
        )
        list(
            JournalEntryLine.objects.filter(journal_entry_id__in=reversal_ids)
            .order_by('pk')
            .select_for_update()
        )


def _validate_candidate(
    period: VATPeriod,
    candidate: VatProjectionCandidate,
    locked_input_fp: str,
) -> str | None:
    if candidate.period_id != period.pk or candidate.tenant_id != period.tenant_id:
        return 'INVALID_CANDIDATE'
    if candidate.engine_version != PROJECTION_ENGINE_VERSION:
        return 'INVALID_CANDIDATE'
    if candidate.mapping_version != PDV_MAPPING_VERSION:
        return 'INVALID_CANDIDATE'
    if candidate.status != VatProjectionStatus.READY:
        return 'INVALID_CANDIDATE'
    if locked_input_fp != candidate.input_fingerprint:
        return 'STALE_PROJECTION'
    if output_fingerprint(candidate.rows) != candidate.output_fingerprint:
        return 'INVALID_CANDIDATE'
    identities = [identity_of(row) for row in candidate.rows]
    if len(identities) != len(set(identities)):
        return 'INVALID_CANDIDATE'
    return None


def _idempotent_existing_run(
    period: VATPeriod,
    candidate: VatProjectionCandidate,
    locked_ledger: list[VATLedgerEntry],
) -> VATProjectionRun | None:
    existing = (
        VATProjectionRun.all_objects.filter(
            vat_period=period,
            status=VATProjectionRunStatus.APPLIED,
            engine_version=candidate.engine_version,
            mapping_version=candidate.mapping_version,
            input_fingerprint=candidate.input_fingerprint,
            output_fingerprint=candidate.output_fingerprint,
        )
        .order_by('-pk')
        .first()
    )
    if existing is None:
        return None
    engine_entries = [
        entry for entry in locked_ledger if entry.origin == VATLedgerOrigin.ENGINE
    ]
    if engine_effect_fingerprint_from_ledger(engine_entries) != engine_effect_fingerprint_from_rows(
        candidate.rows
    ):
        return None
    return existing


def _insert_engine_rows(
    period: VATPeriod,
    candidate: VatProjectionCandidate,
    run: VATProjectionRun,
) -> None:
    from expenses.models import Expense
    from invoices.models import Invoice

    invoice_ct = ContentType.objects.get_for_model(Invoice)
    expense_ct = ContentType.objects.get_for_model(Expense)
    line_ct = ContentType.objects.get_for_model(JournalEntryLine)
    period_ct = ContentType.objects.get_for_model(VATPeriod)
    entry_date = _period_last_day(period)

    to_create: list[VATLedgerEntry] = []
    for row in writable_rows(candidate.rows):
        ct = _source_content_type(row, invoice_ct, expense_ct, line_ct, period_ct)
        oid = resolved_source_object_id(row)
        rule = PDV_MAPPING.get(row.box)
        vat_rate = Decimal('0.00')
        if row.base_amount:
            vat_rate = (row.tax_amount / row.base_amount * Decimal('100')).quantize(Decimal('0.01'))
        to_create.append(
            VATLedgerEntry(
                tenant_id=period.tenant_id,
                vat_period=period,
                ledger_type=row.ledger_type,
                entry_date=entry_date,
                document_number=_document_number(row),
                partner_name='',
                partner_oib='',
                base_amount=row.base_amount * row.sign,
                vat_rate=vat_rate,
                vat_amount=row.tax_amount * row.sign,
                rrif_vat_account=rule.rrif_vat_account if rule else '',
                source_content_type=ct,
                source_object_id=oid,
                vat_box=row.box,
                entry_category=row.category,
                is_manual=False,
                origin=VATLedgerOrigin.ENGINE,
                projection_run=run,
                mapping_version=row.mapping_version,
                source_line_id=row.source_line_id,
            )
        )
    if to_create:
        VATLedgerEntry.all_objects.bulk_create(to_create)


def _source_content_type(row: ProjectedLedgerRow, invoice_ct, expense_ct, line_ct, period_ct):
    if row.source_kind in {'invoice_item', 'invoice'}:
        return invoice_ct
    if row.source_kind == 'expense':
        return expense_ct
    if row.source_kind == 'journal_line':
        return line_ct
    if row.source_kind == 'vat_period':
        return period_ct
    return None


def _document_number(row: ProjectedLedgerRow) -> str:
    line = row.source_line_id if row.source_line_id is not None else 0
    return f'{row.source_kind}:{row.source_id}:{line}:{row.box}'[:50]


def _period_last_day(period: VATPeriod) -> date:
    last_day = calendar.monthrange(period.year, period.month)[1]
    return date(period.year, period.month, last_day)


def _audit_rejection(
    period: VATPeriod,
    candidate: VatProjectionCandidate,
    actor: AbstractBaseUser | None,
    *,
    status: str,
    code: str,
) -> VATProjectionRun:
    return VATProjectionRun.all_objects.create(
        tenant_id=period.tenant_id,
        vat_period=period,
        status=status,
        engine_version=candidate.engine_version or PROJECTION_ENGINE_VERSION,
        mapping_version=candidate.mapping_version or PDV_MAPPING_VERSION,
        input_fingerprint=candidate.input_fingerprint or '',
        output_fingerprint=candidate.output_fingerprint or '',
        classified_count=candidate.classified_count,
        not_relevant_count=candidate.not_relevant_count,
        review_count=candidate.review_count,
        invalid_count=candidate.invalid_count,
        actor=actor if getattr(actor, 'pk', None) else None,
        rejection_code=code,
    )


def _record_failed_run(
    period: VATPeriod,
    candidate: VatProjectionCandidate,
    actor: AbstractBaseUser | None,
    *,
    code: str,
) -> None:
    try:
        with transaction.atomic():
            VATProjectionRun.all_objects.create(
                tenant_id=period.tenant_id,
                vat_period_id=period.pk,
                status=VATProjectionRunStatus.FAILED,
                engine_version=getattr(candidate, 'engine_version', None) or PROJECTION_ENGINE_VERSION,
                mapping_version=getattr(candidate, 'mapping_version', None) or PDV_MAPPING_VERSION,
                input_fingerprint=getattr(candidate, 'input_fingerprint', None) or '',
                output_fingerprint=getattr(candidate, 'output_fingerprint', None) or '',
                classified_count=getattr(candidate, 'classified_count', 0) or 0,
                not_relevant_count=getattr(candidate, 'not_relevant_count', 0) or 0,
                review_count=getattr(candidate, 'review_count', 0) or 0,
                invalid_count=getattr(candidate, 'invalid_count', 0) or 0,
                actor=actor if getattr(actor, 'pk', None) else None,
                rejection_code=code,
            )
    except Exception:
        # Audit must not mask the original failure.
        pass
