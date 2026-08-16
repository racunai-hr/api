"""Internal submission event helpers — public API is SubmissionService."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from accounting.models import (
    PDVSReturn,
    SubmissionEvent,
    SubmissionEventState,
    SubmissionMethod,
    SubmissionSource,
    VATPeriod,
    VATReturnSubmissionMethod,
    ZPReturn,
)
from accounting.services.submission.exceptions import (
    CreateSubmissionEventError,
    DuplicateExternalIdentifierError,
    TransitionSubmissionStateError,
)
from accounting.services.submission.service import (
    SubmissionService,
    next_submission_no,
    transition_submission_state,
)

_VAT_RETURN_METHOD_MAP = {
    VATReturnSubmissionMethod.MANUAL_EP: SubmissionSource.MANUAL,
    VATReturnSubmissionMethod.API: SubmissionSource.API,
    VATReturnSubmissionMethod.IMPORT: SubmissionSource.IMPORT,
}

_SUBMISSION_METHOD_TO_SOURCE = {
    SubmissionMethod.MANUAL: SubmissionSource.MANUAL,
    SubmissionMethod.API: SubmissionSource.API,
    SubmissionMethod.IMPORT: SubmissionSource.IMPORT,
}


def map_vat_return_submission_method(method: str) -> str:
    """Map legacy VATReturnSubmissionMethod values to SubmissionSource."""
    return _VAT_RETURN_METHOD_MAP.get(method, SubmissionSource.MANUAL)


def get_or_create_pdv_s_return(period: VATPeriod) -> PDVSReturn:
    """Return the PDV-S anchor document for a VAT period."""
    pdv_s_return, _ = PDVSReturn.objects.get_or_create(
        tenant=period.tenant,
        vat_period=period,
        defaults={'version': 1},
    )
    return pdv_s_return


def get_latest_zp_return(period: VATPeriod) -> ZPReturn | None:
    """Return the latest ZPReturn draft for a VAT period, if any."""
    return (
        ZPReturn.all_objects.filter(vat_period=period)
        .order_by('-version')
        .first()
    )


def get_or_create_zp_return(period: VATPeriod) -> ZPReturn:
    """Return the latest ZPReturn for a period, creating a draft if none exists."""
    latest = get_latest_zp_return(period)
    if latest is not None:
        return latest
    from accounting.services.tax_forms.zp.zp_returns import create_zp_return_draft

    return create_zp_return_draft(period)


def get_active_submission(document) -> SubmissionEvent | None:
    """Deprecated alias — use SubmissionService.current_submission()."""
    return SubmissionService.current_submission(document)


def get_submission_events(document):
    """Return all submission events for a document ordered by submission_no."""
    content_type = ContentType.objects.get_for_model(document)
    return SubmissionEvent.all_objects.filter(
        tenant=document.tenant,
        content_type=content_type,
        object_id=document.pk,
    ).order_by('submission_no')


@transaction.atomic
def create_submission_event(
    document,
    *,
    document_type: str | None = None,  # noqa: ARG001 — derived from content_type
    destination: str,
    external_identifier: UUID | str,
    submitted_at: datetime,
    submitted_by,
    submission_method: str,
    state: str = SubmissionEventState.SUBMITTED,
    version_confirmed: bool,
    confirmation_attachment=None,
    payload_hash: str | None = None,
    source: str | None = None,
) -> SubmissionEvent:
    """Deprecated wrapper — delegates to SubmissionService.create_event()."""
    resolved_source = source or _SUBMISSION_METHOD_TO_SOURCE.get(
        submission_method,
        SubmissionSource.MANUAL,
    )
    try:
        return SubmissionService.create_event(
            document,
            destination=destination,
            external_identifier=external_identifier,
            submitted_at=submitted_at,
            submitted_by=submitted_by,
            source=resolved_source,
            payload_hash=payload_hash,
            state=state,
            version_confirmed=version_confirmed,
            confirmation_attachment=confirmation_attachment,
        )
    except DuplicateExternalIdentifierError:
        raise
    except CreateSubmissionEventError:
        raise
    except Exception as exc:
        raise CreateSubmissionEventError(str(exc)) from exc
