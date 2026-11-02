"""Mark TZ2 as submitted — delegates to SubmissionService (ADR-0009)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import transaction

from accounting.models import SubmissionDestination, SubmissionSource, TZ2Return
from accounting.services.submission.service import SubmissionService


class MarkTz2SubmittedError(Exception):
    """Raised when TZ2 submission evidence cannot be recorded."""


@transaction.atomic
def mark_tz2_submitted(
    tz2_return: TZ2Return,
    *,
    submitted_at: datetime,
    eporezna_identifier: UUID,
    submitted_by,
    version_confirmed: bool,
    submission_confirmation=None,
) -> TZ2Return:
    """Record manual ePorezna TZ2 submission. Does not lock VATPeriod."""
    try:
        SubmissionService.create_event(
            tz2_return,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=eporezna_identifier,
            submitted_at=submitted_at,
            submitted_by=submitted_by,
            source=SubmissionSource.MANUAL,
            version_confirmed=version_confirmed,
            confirmation_attachment=submission_confirmation,
        )
    except Exception as exc:
        raise MarkTz2SubmittedError(str(exc)) from exc

    tz2_return.refresh_from_db()
    return tz2_return
