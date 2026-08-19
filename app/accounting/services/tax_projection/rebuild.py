"""Production VAT ledger rebuild facade (Gate D)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from accounting.models import VATLedgerEntry, VATPeriod, VATProjectionRunStatus
from accounting.services.tax_projection.apply import apply_vat_projection
from accounting.services.tax_projection.contracts import VatProjectionStatus
from accounting.services.tax_projection.locks import advisory_lock_tenant, lock_tenant_for_projection
from accounting.services.tax_projection.prepare import prepare_vat_projection
from accounting.services.tax_projection.switch import (
    SwitchState,
    VATProjectionSwitchInvalid,
    read_projection_write_switch,
)

logger = logging.getLogger(__name__)


class RebuildOutcome(StrEnum):
    LEGACY = 'LEGACY'
    APPLIED = 'APPLIED'
    REJECTED = 'REJECTED'
    STALE = 'STALE'
    FAILED = 'FAILED'
    NOT_WRITABLE = 'VAT_PERIOD_NOT_WRITABLE'
    SWITCH_INVALID = 'VAT_PROJECTION_SWITCH_INVALID'


@dataclass(frozen=True)
class RebuildResult:
    outcome: RebuildOutcome
    period_id: int | None
    rejection_code: str
    run_id: int | None
    created: int
    total: int
    input_fingerprint: str
    output_fingerprint: str
    message: str

    @property
    def ok(self) -> bool:
        return self.outcome in {RebuildOutcome.LEGACY, RebuildOutcome.APPLIED}

    def exit_code(self) -> int:
        if self.outcome in {RebuildOutcome.LEGACY, RebuildOutcome.APPLIED}:
            return 0
        if self.outcome in {
            RebuildOutcome.REJECTED,
            RebuildOutcome.NOT_WRITABLE,
            RebuildOutcome.STALE,
        }:
            return 1
        if self.outcome == RebuildOutcome.SWITCH_INVALID:
            return 2
        return 3


class VATPeriodNotWritable(RuntimeError):
    code = 'VAT_PERIOD_NOT_WRITABLE'


class VATProjectionWriteEnabled(RuntimeError):
    """Raised when generate_vat_ledger is called while projection write switch is on."""

    code = 'VAT_PROJECTION_WRITE_ENABLED'


def rebuild_vat_ledger(
    tenant,
    year: int,
    month: int,
    *,
    actor: AbstractBaseUser | None = None,
    replace: bool = True,
) -> RebuildResult:
    """Single production writer entry. Lifecycle gate applies before switch branch."""
    with transaction.atomic():
        advisory_lock_tenant(tenant.pk)
        lock_tenant_for_projection(tenant)

        period = (
            VATPeriod.all_objects.select_for_update()
            .filter(tenant_id=tenant.pk, year=year, month=month)
            .first()
        )
        if period is not None and period.status != 'open':
            result = RebuildResult(
                outcome=RebuildOutcome.NOT_WRITABLE,
                period_id=period.pk,
                rejection_code=VATPeriodNotWritable.code,
                run_id=None,
                created=0,
                total=VATLedgerEntry.all_objects.filter(vat_period=period).count(),
                input_fingerprint='',
                output_fingerprint='',
                message=f'PDV {month:02d}/{year}: period status={period.status}',
            )
            logger.warning(
                'vat_rebuild_not_writable tenant=%s period_id=%s status=%s',
                tenant.slug,
                period.pk,
                period.status,
            )
            return result

        switch = read_projection_write_switch(tenant)
        if switch == SwitchState.INVALID:
            result = RebuildResult(
                outcome=RebuildOutcome.SWITCH_INVALID,
                period_id=period.pk if period else None,
                rejection_code=VATProjectionSwitchInvalid.code,
                run_id=None,
                created=0,
                total=0,
                input_fingerprint='',
                output_fingerprint='',
                message='Invalid vat_projection_write switch value',
            )
            logger.warning(
                'vat_rebuild_switch_invalid tenant=%s year=%s month=%s',
                tenant.slug,
                year,
                month,
            )
            return result

        if switch == SwitchState.OFF:
            from accounting.services.vat import generate_vat_ledger

            created, total = generate_vat_ledger(tenant, year, month, replace=replace)
            period = VATPeriod.all_objects.get(tenant=tenant, year=year, month=month)
            logger.info(
                'vat_rebuild_legacy tenant=%s period_id=%s created=%s total=%s',
                tenant.slug,
                period.pk,
                created,
                total,
            )
            return RebuildResult(
                outcome=RebuildOutcome.LEGACY,
                period_id=period.pk,
                rejection_code='',
                run_id=None,
                created=created,
                total=total,
                input_fingerprint='',
                output_fingerprint='',
                message=f'PDV {month:02d}/{year}: legacy {created} new, total {total}',
            )

        # switch ON
        if period is None:
            period = VATPeriod.all_objects.create(
                tenant=tenant, year=year, month=month, status='open',
            )
            period = VATPeriod.all_objects.select_for_update().get(pk=period.pk)

        candidate = prepare_vat_projection(period)
        if candidate.status != VatProjectionStatus.READY:
            code = candidate.primary_rejection_code or candidate.status.value
            outcome = (
                RebuildOutcome.STALE
                if candidate.status == VatProjectionStatus.STALE
                else RebuildOutcome.REJECTED
            )
            logger.warning(
                'vat_rebuild_prepare_rejected tenant=%s period_id=%s code=%s status=%s',
                tenant.slug,
                period.pk,
                code,
                candidate.status,
            )
            return RebuildResult(
                outcome=outcome,
                period_id=period.pk,
                rejection_code=code,
                run_id=None,
                created=0,
                total=VATLedgerEntry.all_objects.filter(vat_period=period).count(),
                input_fingerprint=candidate.input_fingerprint,
                output_fingerprint=candidate.output_fingerprint,
                message=f'PDV {month:02d}/{year}: prepare {candidate.status} ({code})',
            )

        run = apply_vat_projection(period, candidate, actor)
        if run.status == VATProjectionRunStatus.APPLIED:
            engine_count = VATLedgerEntry.all_objects.filter(
                vat_period=period, origin='engine',
            ).count()
            total = VATLedgerEntry.all_objects.filter(vat_period=period).count()
            logger.info(
                'vat_rebuild_applied tenant=%s period_id=%s run_id=%s engine=%s total=%s '
                'input=%s output=%s',
                tenant.slug,
                period.pk,
                run.pk,
                engine_count,
                total,
                run.input_fingerprint[:12],
                run.output_fingerprint[:12],
            )
            return RebuildResult(
                outcome=RebuildOutcome.APPLIED,
                period_id=period.pk,
                rejection_code='',
                run_id=run.pk,
                created=engine_count,
                total=total,
                input_fingerprint=run.input_fingerprint,
                output_fingerprint=run.output_fingerprint,
                message=f'PDV {month:02d}/{year}: applied run={run.pk} engine={engine_count}',
            )

        outcome = RebuildOutcome.STALE if run.status == VATProjectionRunStatus.STALE else RebuildOutcome.REJECTED
        if run.status == VATProjectionRunStatus.FAILED:
            outcome = RebuildOutcome.FAILED
        logger.warning(
            'vat_rebuild_apply_rejected tenant=%s period_id=%s run_id=%s status=%s code=%s',
            tenant.slug,
            period.pk,
            run.pk,
            run.status,
            run.rejection_code,
        )
        return RebuildResult(
            outcome=outcome,
            period_id=period.pk,
            rejection_code=run.rejection_code or run.status,
            run_id=run.pk,
            created=0,
            total=VATLedgerEntry.all_objects.filter(vat_period=period).count(),
            input_fingerprint=run.input_fingerprint,
            output_fingerprint=run.output_fingerprint,
            message=f'PDV {month:02d}/{year}: apply {run.status} ({run.rejection_code})',
        )
