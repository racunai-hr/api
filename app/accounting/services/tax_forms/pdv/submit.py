"""Mark ERP-generated VATReturn as submitted on ePorezna (evidence only)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import transaction

from accounting.models import (
    SubmissionDestination,
    SubmissionSource,
    VATReturn,
    VATReturnStatus,
)
from accounting.services.submission.service import SubmissionService


class MarkVatReturnSubmittedError(Exception):
    """Raised when submission evidence cannot be recorded."""


@transaction.atomic
def mark_vat_return_submitted(
    vat_return: VATReturn,
    *,
    submitted_at: datetime,
    eporezna_identifier: UUID,
    submitted_by,
    version_confirmed: bool,
    submission_confirmation=None,
) -> VATReturn:
    """Record manual ePorezna submission without re-parsing signed XML."""
    if vat_return.status != VATReturnStatus.GENERATED:
        raise MarkVatReturnSubmittedError(
            f'PDV obrazac mora biti u statusu "generirano", trenutno: '
            f'{vat_return.get_status_display()}.',
        )

    try:
        SubmissionService.create_event(
            vat_return,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=eporezna_identifier,
            submitted_at=submitted_at,
            submitted_by=submitted_by,
            source=SubmissionSource.MANUAL,
            version_confirmed=version_confirmed,
            confirmation_attachment=submission_confirmation,
        )
    except Exception as exc:
        raise MarkVatReturnSubmittedError(str(exc)) from exc

    vat_return.refresh_from_db()
    previous_submitted = (
        VATReturn.all_objects.filter(
            vat_period_id=vat_return.vat_period_id,
            status=VATReturnStatus.SUBMITTED,
        )
        .exclude(pk=vat_return.pk)
        .order_by('-version')
    )
    for previous in previous_submitted:
        previous.superseded_by = vat_return
        previous.status = VATReturnStatus.SUPERSEDED
        previous.save(update_fields=['superseded_by', 'status'])
    return vat_return


@transaction.atomic
def record_vat_return_in_processing(
    vat_return: VATReturn,
    *,
    received_at: datetime,
    eporezna_identifier: UUID,
    submitted_by,
) -> VATReturn:
    """Record that ePorezna received a form and left it in manual processing.

    The previously booked return stays submitted. The period booking date stays put.
    """
    if vat_return.status != VATReturnStatus.GENERATED:
        raise MarkVatReturnSubmittedError(
            f'PDV obrazac mora biti u statusu "generirano", trenutno: '
            f'{vat_return.get_status_display()}.',
        )
    booked_at = vat_return.vat_period.submitted_at
    vat_return.status = VATReturnStatus.IN_PROCESSING
    vat_return.save(update_fields=['status'])
    try:
        SubmissionService.create_event(
            vat_return,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=eporezna_identifier,
            submitted_at=received_at,
            submitted_by=submitted_by,
            source=SubmissionSource.MANUAL,
            version_confirmed=True,
            book_period=False,
        )
    except Exception as exc:
        raise MarkVatReturnSubmittedError(str(exc)) from exc
    period = vat_return.vat_period
    period.refresh_from_db()
    if period.submitted_at != booked_at:
        period.submitted_at = booked_at
        period.save(update_fields=['submitted_at'])
    vat_return.refresh_from_db()
    return vat_return
