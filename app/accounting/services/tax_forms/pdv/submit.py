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
    return vat_return
