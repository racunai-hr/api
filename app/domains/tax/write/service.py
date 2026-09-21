"""Thin write adapters for the locked PDV / PDV-S REST map."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from accounting.models import PDVSReturn, SubmissionEvent, TZ2Return, VATPeriod, VATReturn
from accounting.services.submission.exceptions import (
    AttachConfirmationError,
    CreateSubmissionEventError,
    DuplicateExternalIdentifierError,
)
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.correction import (
    PreparePdvCorrectionError,
    prepare_pdv_correction,
    working_return,
)
from accounting.services.tax_forms.pdv.integrity import (
    VatReturnOutOfSyncError,
    assert_vat_return_sync,
    check_vat_return_integrity,
)
from accounting.services.tax_forms.pdv.submit import (
    MarkVatReturnSubmittedError,
    mark_vat_return_submitted,
)
from accounting.services.tax_forms.pdv.validation import PdvSchemaValidationError
from accounting.services.tax_forms.pdv.vat_returns import create_vat_return_draft
from accounting.services.tax_forms.pdv_s.submit import MarkPdvSSubmittedError, mark_pdv_s_submitted
from accounting.services.tax_forms.pdv_s.validation import PdvSSchemaValidationError
from accounting.services.tax_projection.rebuild import (
    VATPeriodNotWritable,
    rebuild_vat_ledger,
)
from domains.tax.read.dto import parse_period_key, period_key


class TaxNotFound(Exception):
    """Resource missing for this tenant/period — views map to 404."""


class TaxConflict(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class TaxBadRequest(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def require_period_key(raw: str) -> tuple[int, int]:
    parsed = parse_period_key(raw)
    if parsed is None:
        raise TaxNotFound()
    return parsed


def rebuild_period_ledger(tenant, raw_period: str, *, actor) -> dict:
    year, month = require_period_key(raw_period)
    try:
        result = rebuild_vat_ledger(tenant, year, month, actor=actor)
    except VATPeriodNotWritable as exc:
        raise TaxConflict(str(exc)) from exc
    if not result.ok:
        raise TaxConflict(result.message)
    return {'created': result.created, 'total': result.total}


def create_period_draft(period: VATPeriod) -> dict:
    if period.status == 'submitted':
        raise TaxConflict('PDV razdoblje je predano. Ispravak ide kroz Pripremi ispravak.')
    try:
        vat_return = create_vat_return_draft(period)
    except (PdvSchemaValidationError, ValueError) as exc:
        raise TaxBadRequest(str(exc)) from exc
    integrity = check_vat_return_integrity(vat_return)
    return {
        'period': period_key(period),
        'return_version': vat_return.version,
        'return_status': vat_return.status,
        'xml_integrity': integrity.status,
    }


def prepare_period_correction(period: VATPeriod, *, actor) -> dict:
    try:
        payload = prepare_pdv_correction(period, actor=actor)
    except PreparePdvCorrectionError as exc:
        raise TaxConflict(exc.detail) from exc
    return {
        'period': period_key(period),
        **payload,
    }


def pdv_unsigned_xml_bytes(period: VATPeriod) -> tuple[bytes, str]:
    vat_return = working_return(period)
    if vat_return is None:
        raise TaxNotFound()
    try:
        assert_vat_return_sync(vat_return)
    except VatReturnOutOfSyncError as exc:
        raise TaxConflict(str(exc)) from exc
    xml_bytes = vat_return.xml_unsigned.open('rb').read()
    filename = f'PDV_{period_key(period)}_v{vat_return.version}.xml'
    return xml_bytes, filename


def submit_pdv_period(
    period: VATPeriod,
    *,
    user,
    eporezna_identifier: UUID | None,
    submitted_at: datetime | None,
    return_version: int,
    submitted_xml=None,
) -> dict:
    vat_return = working_return(period)
    if vat_return is None:
        raise TaxNotFound()
    if vat_return.version != return_version:
        raise TaxConflict('return_version ne odgovara trenutnom PDV obrascu.')
    if submitted_xml is None and (eporezna_identifier is None or submitted_at is None):
        raise TaxBadRequest('eporezna_identifier i submitted_at su obavezni bez predanog XML-a.')
    if submitted_xml is not None:
        validation = SubmissionService.validate(submitted_xml, document=vat_return)
        if validation.errors:
            raise TaxConflict('; '.join(validation.errors))
        submitted_xml.seek(0)
    if eporezna_identifier is None:
        eporezna_identifier = uuid4()
    if submitted_at is None:
        submitted_at = timezone.now()
    try:
        with transaction.atomic():
            vat_return = mark_vat_return_submitted(
                vat_return,
                submitted_at=submitted_at,
                eporezna_identifier=eporezna_identifier,
                submitted_by=user,
                version_confirmed=True,
            )
            event = SubmissionService.current_submission(vat_return)
            if event is None:
                raise TaxConflict('Predaja nije zabilježena.')
            if submitted_xml is not None:
                event = SubmissionService.attach_confirmation(
                    event,
                    submitted_xml,
                    uploaded_by=user,
                )
    except (MarkVatReturnSubmittedError, DuplicateExternalIdentifierError, CreateSubmissionEventError) as exc:
        raise TaxConflict(str(exc)) from exc
    except AttachConfirmationError as exc:
        raise TaxConflict(str(exc)) from exc
    return _submission_dto(event)


def submit_pdv_s_period(
    period: VATPeriod,
    *,
    user,
    eporezna_identifier: UUID,
    submitted_at: datetime,
) -> dict:
    try:
        event = mark_pdv_s_submitted(
            period,
            submitted_at=submitted_at,
            eporezna_identifier=eporezna_identifier,
            submitted_by=user,
            version_confirmed=True,
        )
    except (MarkPdvSSubmittedError, DuplicateExternalIdentifierError, CreateSubmissionEventError) as exc:
        raise TaxConflict(str(exc)) from exc
    return _submission_dto(event)


def _is_tz2_xml_upload(file_obj) -> bool:
    name = (getattr(file_obj, 'name', '') or '').lower()
    return name.endswith('.xml')


def _store_tz2_submitted_xml(document: TZ2Return, event: SubmissionEvent) -> None:
    attachment = event.confirmation_attachment
    if not attachment:
        return
    attachment.open('rb')
    try:
        content = attachment.read()
    finally:
        attachment.seek(0)
    from django.core.files.base import ContentFile

    document.xml_submitted.save(
        f'TZ2_{document.tax_year}_submitted.xml',
        ContentFile(content),
        save=True,
    )


def attach_period_confirmation(tenant, event_uuid, file_obj, *, uploaded_by) -> dict:
    event = (
        SubmissionEvent.all_objects.filter(tenant=tenant, event_uuid=event_uuid)
        .first()
    )
    if event is None:
        raise TaxNotFound()
    document = event.document
    if not isinstance(document, (VATReturn, PDVSReturn, TZ2Return)):
        raise TaxNotFound()
    if file_obj is None:
        raise TaxBadRequest('confirmation file je obavezan.')
    try:
        event = SubmissionService.attach_confirmation(
            event,
            file_obj,
            uploaded_by=uploaded_by,
        )
    except AttachConfirmationError as exc:
        raise TaxConflict(str(exc)) from exc
    if isinstance(document, TZ2Return) and _is_tz2_xml_upload(file_obj):
        _store_tz2_submitted_xml(document, event)
    return {
        'event_uuid': str(event.event_uuid),
        'has_confirmation': bool(event.confirmation_attachment),
    }


def pdv_s_xml_bytes(period: VATPeriod) -> tuple[bytes, str]:
    from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows
    from accounting.services.tax_forms.pdv_s.render import render_pdv_s_xml
    from accounting.services.tax_forms.pdv_s.validation import validate_pdv_s_xml

    try:
        payload = aggregate_pdv_s_rows(period)
        xml_bytes = render_pdv_s_xml(payload)
        validate_pdv_s_xml(xml_bytes)
    except PdvSSchemaValidationError as exc:
        raise TaxBadRequest(str(exc)) from exc
    except ValueError as exc:
        raise TaxBadRequest(str(exc)) from exc
    filename = (
        f'PDV-S_{payload.taxpayer.oib}_'
        f'{payload.period_from:%Y%m%d}-{payload.period_to:%Y%m%d}.xml'
    )
    return xml_bytes, filename


def _submission_dto(event: SubmissionEvent) -> dict:
    submitted_at = event.submitted_at
    return {
        'event_uuid': str(event.event_uuid),
        'external_identifier': str(event.external_identifier),
        'submitted_at': submitted_at.isoformat() if submitted_at is not None else None,
        'has_confirmation': bool(event.confirmation_attachment),
    }
