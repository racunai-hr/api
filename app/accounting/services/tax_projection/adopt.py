"""Atomic legacy → engine handoff (Gate E0). Prevents legacy+engine double ledger."""

from __future__ import annotations

import logging

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from accounting.models import (
    VATLedgerEntry,
    VATLedgerOrigin,
    VATPeriod,
    VATProjectionRun,
    VATProjectionRunStatus,
)
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING_VERSION
from accounting.services.tax_projection.apply import (
    PostWriteFingerprintMismatch,
    _attach_failed_run,
    _audit_rejection,
    _idempotent_existing_run,
    _insert_engine_rows,
    _lock_sources,
    _record_failed_run,
    _validate_candidate,
)
from accounting.services.tax_projection.business import (
    business_multiset_from_candidate,
    business_multiset_from_entries,
    business_multisets_equal,
    count_box,
)
from accounting.services.tax_projection.contracts import (
    PROJECTION_ENGINE_VERSION,
    VatProjectionCandidate,
)
from accounting.services.tax_projection.fingerprints import (
    engine_effect_fingerprint_from_ledger,
    engine_effect_fingerprint_from_rows,
)
from accounting.services.tax_projection.locks import advisory_lock_tenant, lock_tenant_for_projection
from accounting.services.tax_projection.snapshot import (
    documents_from_snapshot,
    load_snapshot,
    snapshot_fingerprint,
)

logger = logging.getLogger(__name__)

LEGACY_CANDIDATE_MISMATCH = 'LEGACY_CANDIDATE_MISMATCH'
LEGACY_HANDOFF_REQUIRED = 'LEGACY_HANDOFF_REQUIRED'
AMBIGUOUS_LEDGER_ORIGINS = 'AMBIGUOUS_LEDGER_ORIGINS'
INVALID_610 = 'INVALID_610'


def period_needs_legacy_handoff(period: VATPeriod) -> bool:
    """True when non-manual legacy rows still own the generated ledger."""
    return VATLedgerEntry.all_objects.filter(
        vat_period=period,
        origin=VATLedgerOrigin.LEGACY,
        is_manual=False,
    ).exists()


def period_has_ambiguous_origins(period: VATPeriod) -> bool:
    """Engine and legacy generated rows together — unsafe for plain apply or adopt."""
    has_legacy = period_needs_legacy_handoff(period)
    has_engine = VATLedgerEntry.all_objects.filter(
        vat_period=period,
        origin=VATLedgerOrigin.ENGINE,
    ).exists()
    return has_legacy and has_engine


def adopt_legacy_projection(
    period: VATPeriod,
    candidate: VatProjectionCandidate,
    actor: AbstractBaseUser | None = None,
) -> VATProjectionRun:
    """Replace non-manual legacy with engine rows in one atomic cutover.

    Manual rows keep the same PK. Rejects without ledger mutation when the
    business multiset does not match (audit REJECTED run may still be written).
    """
    try:
        with transaction.atomic():
            advisory_lock_tenant(period.tenant_id)
            lock_tenant_for_projection(period.tenant)
            return _adopt_in_transaction(period, candidate, actor)
    except Exception as exc:
        if isinstance(exc, PostWriteFingerprintMismatch):
            _attach_failed_run(
                exc,
                _record_failed_run(
                    period, candidate, actor, code='POST_WRITE_FINGERPRINT_MISMATCH',
                ),
            )
            raise
        _attach_failed_run(
            exc,
            _record_failed_run(period, candidate, actor, code='ADOPT_FAILED'),
        )
        raise


def _adopt_in_transaction(
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

    if period_has_ambiguous_origins(locked_period):
        return _audit_rejection(
            locked_period,
            candidate,
            actor,
            status=VATProjectionRunStatus.REJECTED,
            code=AMBIGUOUS_LEDGER_ORIGINS,
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

    # Already fully engine-owned and matching → same idempotency as apply.
    if not period_needs_legacy_handoff(locked_period):
        existing = _idempotent_existing_run(locked_period, candidate, locked_ledger)
        if existing is not None:
            return existing

    non_manual = [
        entry
        for entry in locked_ledger
        if entry.origin != VATLedgerOrigin.MANUAL and not entry.is_manual
    ]
    candidate_ms = business_multiset_from_candidate(candidate)
    ledger_ms = business_multiset_from_entries(non_manual)

    if count_box(candidate_ms, '610') > 1 or count_box(ledger_ms, '610') > 1:
        return _audit_rejection(
            locked_period,
            candidate,
            actor,
            status=VATProjectionRunStatus.REJECTED,
            code=INVALID_610,
        )
    if count_box(candidate_ms, '610') != count_box(ledger_ms, '610'):
        return _audit_rejection(
            locked_period,
            candidate,
            actor,
            status=VATProjectionRunStatus.REJECTED,
            code=INVALID_610,
        )

    if not business_multisets_equal(candidate_ms, ledger_ms):
        logger.warning(
            'vat_adopt_mismatch tenant=%s period_id=%s candidate_keys=%s ledger_keys=%s',
            locked_period.tenant.slug,
            locked_period.pk,
            sum(candidate_ms.values()),
            sum(ledger_ms.values()),
        )
        return _audit_rejection(
            locked_period,
            candidate,
            actor,
            status=VATProjectionRunStatus.REJECTED,
            code=LEGACY_CANDIDATE_MISMATCH,
        )

    run = VATProjectionRun.all_objects.create(
        tenant_id=locked_period.tenant_id,
        vat_period=locked_period,
        status=VATProjectionRunStatus.PREPARED,
        engine_version=candidate.engine_version or PROJECTION_ENGINE_VERSION,
        mapping_version=candidate.mapping_version or PDV_MAPPING_VERSION,
        input_fingerprint=candidate.input_fingerprint,
        output_fingerprint=candidate.output_fingerprint,
        classified_count=candidate.classified_count,
        not_relevant_count=candidate.not_relevant_count,
        review_count=candidate.review_count,
        invalid_count=candidate.invalid_count,
        actor=actor if getattr(actor, 'pk', None) else None,
        rejection_code='',
    )

    manual_pks = [
        entry.pk
        for entry in locked_ledger
        if entry.origin == VATLedgerOrigin.MANUAL or entry.is_manual
    ]

    VATLedgerEntry.all_objects.filter(vat_period=locked_period).exclude(
        pk__in=manual_pks,
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

    if VATLedgerEntry.all_objects.filter(
        vat_period=locked_period,
        origin=VATLedgerOrigin.LEGACY,
        is_manual=False,
    ).exists():
        raise PostWriteFingerprintMismatch('LEGACY_REMAINING_AFTER_ADOPT')

    post_ms = business_multiset_from_entries(engine_entries)
    if not business_multisets_equal(candidate_ms, post_ms):
        raise PostWriteFingerprintMismatch('POST_ADOPT_BUSINESS_MISMATCH')

    run.status = VATProjectionRunStatus.APPLIED
    run.applied_at = timezone.now()
    run.save(update_fields=['status', 'applied_at'])
    logger.info(
        'vat_adopt_applied tenant=%s period_id=%s run_id=%s engine=%s manuals=%s',
        locked_period.tenant.slug,
        locked_period.pk,
        run.pk,
        len(engine_entries),
        len(manual_pks),
    )
    return run
