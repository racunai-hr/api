"""Prepare a PDV correction draft without reopening VATPeriod (ADR-0027)."""

from __future__ import annotations

from django.db import transaction

from accounting.models import VATPeriod, VATReturnStatus
from accounting.services.tax_forms.pdv.integrity import check_vat_return_integrity
from accounting.services.tax_forms.pdv.validation import PdvSchemaValidationError
from accounting.services.tax_forms.pdv.vat_returns import create_vat_return_draft
from accounting.services.tax_projection.rebuild import rebuild_vat_ledger

CORRECTION_ALREADY_IN_PROGRESS = 'CORRECTION_ALREADY_IN_PROGRESS'
CORRECTION_REQUIRES_SUBMITTED_RETURN = 'CORRECTION_REQUIRES_SUBMITTED_RETURN'
CORRECTION_REQUIRES_SUBMITTED_PERIOD = 'CORRECTION_REQUIRES_SUBMITTED_PERIOD'


class PreparePdvCorrectionError(Exception):
    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail or code
        super().__init__(self.detail)


def working_return(period: VATPeriod):
    """Unsigned XML / submit target: generated correction if present, else current_return."""
    latest = period.latest_return
    if latest is not None and latest.status == VATReturnStatus.GENERATED:
        return latest
    return period.current_return


def correction_in_progress(period: VATPeriod) -> bool:
    latest = period.latest_return
    current = period.current_return
    if latest is None or current is None:
        return False
    return (
        latest.status == VATReturnStatus.GENERATED
        and current.status == VATReturnStatus.SUBMITTED
        and latest.pk != current.pk
    )


@transaction.atomic
def prepare_pdv_correction(period: VATPeriod, *, actor) -> dict:
    locked = VATPeriod.all_objects.select_for_update().get(pk=period.pk)
    if locked.status != 'submitted':
        raise PreparePdvCorrectionError(CORRECTION_REQUIRES_SUBMITTED_PERIOD)

    current = locked.current_return
    latest = locked.latest_return
    if current is None or current.status != VATReturnStatus.SUBMITTED:
        raise PreparePdvCorrectionError(CORRECTION_REQUIRES_SUBMITTED_RETURN)
    if (
        latest is not None
        and latest.status == VATReturnStatus.GENERATED
        and latest.pk != current.pk
    ):
        raise PreparePdvCorrectionError(CORRECTION_ALREADY_IN_PROGRESS)

    result = rebuild_vat_ledger(
        locked.tenant,
        locked.year,
        locked.month,
        actor=actor,
        replace=True,
        allow_submitted_correction=True,
    )
    if not result.ok:
        raise PreparePdvCorrectionError(result.rejection_code or 'REBUILD_FAILED', result.message)

    locked.refresh_from_db()
    if locked.status != 'submitted':
        raise PreparePdvCorrectionError(
            'PERIOD_STATUS_CHANGED',
            'PDV razdoblje ne smije promijeniti status tijekom ispravka.',
        )

    try:
        vat_return = create_vat_return_draft(locked)
    except (PdvSchemaValidationError, ValueError) as exc:
        raise PreparePdvCorrectionError('DRAFT_FAILED', str(exc)) from exc

    locked.refresh_from_db()
    if locked.status != 'submitted':
        raise PreparePdvCorrectionError(
            'PERIOD_STATUS_CHANGED',
            'PDV razdoblje ne smije promijeniti status tijekom ispravka.',
        )

    integrity = check_vat_return_integrity(vat_return)
    return {
        'return_version': vat_return.version,
        'return_status': vat_return.status,
        'xml_integrity': integrity.status,
    }
