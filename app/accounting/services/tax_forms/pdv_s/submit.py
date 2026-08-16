"""Mark PDV-S as submitted on ePorezna (evidence only)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import transaction

from accounting.models import (
    SubmissionDestination,
    SubmissionEvent,
    SubmissionSource,
    VATPeriod,
    VATReturnSubmissionMethod,
)
from accounting.services.submission.events import get_or_create_pdv_s_return
from accounting.services.submission.service import SubmissionService


class MarkPdvSSubmittedError(Exception):
    """Raised when PDV-S submission evidence cannot be recorded."""


def _map_legacy_method(method: str) -> str:
    if method == VATReturnSubmissionMethod.API:
        return SubmissionSource.API
    if method == VATReturnSubmissionMethod.IMPORT:
        return SubmissionSource.IMPORT
    return SubmissionSource.MANUAL


@transaction.atomic
def mark_pdv_s_submitted(
    period: VATPeriod,
    *,
    submitted_at: datetime,
    eporezna_identifier: UUID,
    submitted_by,
    version_confirmed: bool,
    submission_confirmation=None,
    submission_method: str = VATReturnSubmissionMethod.MANUAL_EP,
) -> SubmissionEvent:
    """Record manual ePorezna PDV-S submission without XML parsing."""
    VATPeriod.all_objects.select_for_update().get(pk=period.pk)
    pdv_s_return = get_or_create_pdv_s_return(period)

    try:
        return SubmissionService.create_event(
            pdv_s_return,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=eporezna_identifier,
            submitted_at=submitted_at,
            submitted_by=submitted_by,
            source=_map_legacy_method(submission_method),
            version_confirmed=version_confirmed,
            confirmation_attachment=submission_confirmation,
        )
    except Exception as exc:
        raise MarkPdvSSubmittedError(str(exc)) from exc
