"""Mark ZP as submitted — delegates to SubmissionService (ADR-0009)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import transaction

from accounting.models import (
    SubmissionDestination,
    SubmissionEvent,
    SubmissionSource,
    VATPeriod,
    ZPReturn,
)
from accounting.services.submission.service import SubmissionService


class MarkZpSubmittedError(Exception):
    """Raised when ZP submission evidence cannot be recorded."""


@transaction.atomic
def mark_zp_submitted(
    zp_return: ZPReturn,
    *,
    submitted_at: datetime,
    eporezna_identifier: UUID,
    submitted_by,
    version_confirmed: bool,
    submission_confirmation=None,
) -> ZPReturn:
    """Record manual ePorezna ZP submission without re-parsing signed XML."""
    try:
        SubmissionService.create_event(
            zp_return,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=eporezna_identifier,
            submitted_at=submitted_at,
            submitted_by=submitted_by,
            source=SubmissionSource.MANUAL,
            version_confirmed=version_confirmed,
            confirmation_attachment=submission_confirmation,
        )
    except Exception as exc:
        raise MarkZpSubmittedError(str(exc)) from exc

    zp_return.refresh_from_db()
    return zp_return


@transaction.atomic
def mark_zp_submitted_for_period(
    period: VATPeriod,
    *,
    submitted_at: datetime,
    eporezna_identifier: UUID,
    submitted_by,
    version_confirmed: bool,
    submission_confirmation=None,
) -> SubmissionEvent:
    """Record ZP submission for the latest draft version of a period."""
    from accounting.services.submission.events import get_latest_zp_return

    zp_return = get_latest_zp_return(period)
    if zp_return is None:
        raise MarkZpSubmittedError(
            f'Nema ZP drafta za razdoblje {period.month:02d}/{period.year}.',
        )
    mark_zp_submitted(
        zp_return,
        submitted_at=submitted_at,
        eporezna_identifier=eporezna_identifier,
        submitted_by=submitted_by,
        version_confirmed=version_confirmed,
        submission_confirmation=submission_confirmation,
    )
    from accounting.services.submission.service import SubmissionService

    event = SubmissionService.current_submission(zp_return)
    if event is None:
        raise MarkZpSubmittedError('Submission event nije kreiran.')
    return event
