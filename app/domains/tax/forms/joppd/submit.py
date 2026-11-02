"""Mark JOPPD as submitted — delegates to SubmissionService (ADR-0009)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import transaction

from accounting.models import JOPPDReturn, SubmissionDestination, SubmissionEvent, SubmissionSource
from accounting.services.submission.service import SubmissionService
from domains.tax.forms.joppd.joppd_returns import get_or_create_joppd_return


class MarkJoppdSubmittedError(Exception):
    """Raised when JOPPD submission evidence cannot be recorded."""


@transaction.atomic
def mark_joppd_submitted(
    joppd_return: JOPPDReturn,
    *,
    submitted_at: datetime,
    eporezna_identifier: UUID,
    submitted_by,
    version_confirmed: bool,
    submission_confirmation=None,
) -> JOPPDReturn:
    """Record manual ePorezna JOPPD submission without re-parsing signed XML."""
    try:
        SubmissionService.create_event(
            joppd_return,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=eporezna_identifier,
            submitted_at=submitted_at,
            submitted_by=submitted_by,
            source=SubmissionSource.MANUAL,
            version_confirmed=version_confirmed,
            confirmation_attachment=submission_confirmation,
            book_period=False,
        )
    except Exception as exc:
        raise MarkJoppdSubmittedError(str(exc)) from exc

    joppd_return.refresh_from_db()
    return joppd_return


@transaction.atomic
def mark_joppd_submitted_for_period(
    tenant,
    year: int,
    month: int,
    *,
    submitted_at: datetime,
    eporezna_identifier: UUID,
    submitted_by,
    version_confirmed: bool,
    submission_confirmation=None,
) -> SubmissionEvent:
    """Record JOPPD submission for the latest draft version of tenant/year/month."""
    joppd_return = get_or_create_joppd_return(tenant, year, month)
    mark_joppd_submitted(
        joppd_return,
        submitted_at=submitted_at,
        eporezna_identifier=eporezna_identifier,
        submitted_by=submitted_by,
        version_confirmed=version_confirmed,
        submission_confirmation=submission_confirmation,
    )
    event = SubmissionService.current_submission(joppd_return)
    if event is None:
        raise MarkJoppdSubmittedError('Submission event nije kreiran.')
    return event
