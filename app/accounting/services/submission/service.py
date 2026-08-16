"""SubmissionService — single entry point for tax form submission audit (ADR-0009)."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Max
from lxml import etree

from accounting.models import (
    SubmissionEvent,
    SubmissionEventState,
    TaxDocumentType,
    VATPeriod,
    VATReturnStatus,
    document_type_for_content_type,
)
from accounting.services.submission.exceptions import (
    AttachConfirmationError,
    CreateSubmissionEventError,
    DuplicateExternalIdentifierError,
    SupersedeSubmissionError,
    TransitionSubmissionStateError,
)
from accounting.services.submission.protocol import SubmissionDocument
from accounting.services.tax_forms.pdv.canonical import payload_hash as pdv_payload_hash
from accounting.services.tax_forms.pdv.import_return import payload_from_snapshot
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.validation import (
    PdvSchemaValidationError,
    validate_pdv_obrazac_xml,
)
from accounting.services.tax_forms.pdv_s.parse import parse_pdv_s_xml
from accounting.services.tax_forms.pdv_s.validation import (
    PdvSSchemaValidationError,
    validate_pdv_s_xml,
)

logger = logging.getLogger(__name__)

_METADATA_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'

_ALLOWED_NON_XML_EXTENSIONS = frozenset({
    '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.zip', '.txt',
})


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve_payload_hash(document: SubmissionDocument, explicit: str | None) -> str:
    if explicit:
        return explicit
    document_hash = document.get_payload_hash()
    if not document_hash:
        raise CreateSubmissionEventError(
            'payload_hash je obavezan — dokument nema hash ili ga proslijedite eksplicitno.',
        )
    return document_hash


def _assert_submission_document(document) -> SubmissionDocument:
    if not hasattr(document, 'get_period'):
        raise CreateSubmissionEventError(
            f'Dokument {type(document).__name__} ne implementira SubmissionDocument protokol.',
        )
    return document


def _xml_document_uuid(xml_bytes: bytes) -> UUID | None:
    root = etree.fromstring(xml_bytes)
    identifier = root.find(f'.//{{{_METADATA_NS}}}Identifikator')
    if identifier is None or not identifier.text:
        return None
    try:
        return UUID(identifier.text.strip())
    except ValueError:
        return None


def _is_xml_attachment(file_obj) -> bool:
    name = getattr(file_obj, 'name', '') or ''
    content_type = getattr(file_obj, 'content_type', '') or ''
    if name.lower().endswith('.xml'):
        return True
    return content_type in {'application/xml', 'text/xml'}


def _hash_from_xml_bytes(document_type: str, xml_bytes: bytes) -> str:
    if document_type == TaxDocumentType.PDV:
        parsed = parse_pdv_obrazac_xml(xml_bytes)
        return pdv_payload_hash(parsed)
    if document_type == TaxDocumentType.PDV_S:
        from accounting.services.submission.protocol import pdv_s_payload_hash_from_xml

        return pdv_s_payload_hash_from_xml(xml_bytes)
    if document_type == TaxDocumentType.ZP:
        from accounting.services.submission.protocol import zp_payload_hash_from_xml

        return zp_payload_hash_from_xml(xml_bytes)
    raise ValueError(f'Nepodržana XML validacija za tip {document_type}')


def _validate_pdv_xml(xml_bytes: bytes, *, document, period) -> ValidationResult:
    result = ValidationResult(valid=True)
    try:
        validate_pdv_obrazac_xml(xml_bytes, signed=True)
    except PdvSchemaValidationError as exc:
        result.valid = False
        result.errors.append(str(exc))
        return result

    parsed = parse_pdv_obrazac_xml(xml_bytes)
    if parsed.period_from.year != period.year or parsed.period_from.month != period.month:
        result.valid = False
        result.errors.append(
            f'XML razdoblje {parsed.period_from:%m/%Y} ne odgovara '
            f'PDV razdoblju {period.month:02d}/{period.year}.',
        )

    from settings.models import CompanySettings

    settings = CompanySettings.all_objects.filter(tenant=period.tenant).first()
    if settings and parsed.taxpayer.oib != settings.vat_number:
        result.valid = False
        result.errors.append(
            f'OIB u XML-u ({parsed.taxpayer.oib}) ne odgovara OIB-u tvrtke '
            f'({settings.vat_number}).',
        )

    if hasattr(document, 'payload_snapshot') and document.payload_snapshot:
        draft = payload_from_snapshot(document.payload_snapshot)
        imported_hash = pdv_payload_hash(parsed)
        if imported_hash != document.get_payload_hash():
            result.warnings.append(
                'Hash XML-a ne odgovara hashu drafta u ERP-u — provjerite verziju obrasca.',
            )

    xml_doc_uuid = _xml_document_uuid(xml_bytes)
    if xml_doc_uuid is not None:
        result.warnings.append(
            f'Metapodaci XML Identifikator={xml_doc_uuid} — ne koristi se kao portal UUID.',
        )

    return result


def _validate_pdv_s_xml(xml_bytes: bytes, *, period) -> ValidationResult:
    result = ValidationResult(valid=True)
    try:
        validate_pdv_s_xml(xml_bytes, signed=True)
    except PdvSSchemaValidationError as exc:
        result.valid = False
        result.errors.append(str(exc))
        return result

    parsed = parse_pdv_s_xml(xml_bytes)
    if not parsed.period_from.startswith(f'{period.year}-{period.month:02d}'):
        result.valid = False
        result.errors.append(
            f'XML razdoblje {parsed.period_from} ne odgovara '
            f'PDV razdoblju {period.month:02d}/{period.year}.',
        )

    from settings.models import CompanySettings

    settings = CompanySettings.all_objects.filter(tenant=period.tenant).first()
    if settings and parsed.oib != settings.vat_number:
        result.valid = False
        result.errors.append(
            f'OIB u XML-u ({parsed.oib}) ne odgovara OIB-u tvrtke ({settings.vat_number}).',
        )

    return result


def _validate_zp_xml(xml_bytes: bytes, *, document, period) -> ValidationResult:
    from accounting.services.tax_forms.zp.parse import parse_zp_xml
    from accounting.services.tax_forms.zp.validation import (
        ZpSchemaValidationError,
        validate_zp_xml,
    )

    result = ValidationResult(valid=True)
    try:
        validate_zp_xml(xml_bytes, signed=True)
    except ZpSchemaValidationError as exc:
        result.valid = False
        result.errors.append(str(exc))
        return result

    parsed = parse_zp_xml(xml_bytes)
    if parsed.period_from.year != period.year or parsed.period_from.month != period.month:
        result.valid = False
        result.errors.append(
            f'XML razdoblje {parsed.period_from:%m/%Y} ne odgovara '
            f'PDV razdoblju {period.month:02d}/{period.year}.',
        )

    from settings.models import CompanySettings

    settings = CompanySettings.all_objects.filter(tenant=period.tenant).first()
    if settings and parsed.taxpayer.oib != settings.vat_number:
        result.valid = False
        result.errors.append(
            f'OIB u XML-u ({parsed.taxpayer.oib}) ne odgovara OIB-u tvrtke '
            f'({settings.vat_number}).',
        )

    if hasattr(document, 'payload_snapshot') and document.payload_snapshot:
        from accounting.services.tax_forms.zp.canonical import payload_hash

        imported_hash = payload_hash(parsed)
        if imported_hash != document.get_payload_hash():
            result.warnings.append(
                'Hash XML-a ne odgovara hashu drafta u ERP-u — provjerite verziju obrasca.',
            )

    xml_doc_uuid = _xml_document_uuid(xml_bytes)
    if xml_doc_uuid is not None:
        result.warnings.append(
            f'Metapodaci XML Identifikator={xml_doc_uuid} — ne koristi se kao portal UUID.',
        )

    return result


def next_submission_no(document) -> int:
    """Allocate the next submission number for a document (caller must hold lock)."""
    content_type = ContentType.objects.get_for_model(document)
    current_max = (
        SubmissionEvent.all_objects.filter(
            tenant=document.tenant,
            content_type=content_type,
            object_id=document.pk,
        ).aggregate(max_no=Max('submission_no'))['max_no']
    )
    return (current_max or 0) + 1


@transaction.atomic
def transition_submission_state(
    event: SubmissionEvent,
    new_state: str,
    *,
    transitioned_by,
) -> SubmissionEvent:
    """The only allowed way to change SubmissionEvent.state."""
    allowed = {
        SubmissionEventState.PENDING: {
            SubmissionEventState.SUBMITTED,
            SubmissionEventState.REJECTED,
            SubmissionEventState.CANCELLED,
        },
    }
    if new_state not in allowed.get(event.state, frozenset()):
        raise TransitionSubmissionStateError(
            f'Prijelaz {event.state} → {new_state} nije dopušten.',
        )

    SubmissionEvent.all_objects.select_for_update().get(pk=event.pk)
    event.state = new_state
    event._allow_state_transition = True  # noqa: SLF001
    event.save(update_fields=['state'])
    return event


class SubmissionService:
    @staticmethod
    def current_submission(document) -> SubmissionEvent | None:
        """Return the latest submitted event for a document."""
        content_type = ContentType.objects.get_for_model(document)
        return (
            SubmissionEvent.all_objects.filter(
                tenant=document.tenant,
                content_type=content_type,
                object_id=document.pk,
                state=SubmissionEventState.SUBMITTED,
            )
            .order_by('-submission_no')
            .first()
        )

    @staticmethod
    def validate(
        file_obj,
        *,
        document,
        event: SubmissionEvent | None = None,
    ) -> ValidationResult:
        """Validate a confirmation attachment without side effects."""
        if _is_xml_attachment(file_obj):
            xml_bytes = file_obj.read()
            file_obj.seek(0)
            content_type = ContentType.objects.get_for_model(document)
            document_type = document_type_for_content_type(content_type)
            period = document.get_period()

            if document_type == TaxDocumentType.PDV:
                result = _validate_pdv_xml(xml_bytes, document=document, period=period)
            elif document_type == TaxDocumentType.PDV_S:
                result = _validate_pdv_s_xml(xml_bytes, period=period)
            elif document_type == TaxDocumentType.ZP:
                result = _validate_zp_xml(xml_bytes, document=document, period=period)
            else:
                return ValidationResult(
                    valid=False,
                    errors=[f'XML validacija nije podržana za tip {document_type}.'],
                )

            if event is not None and event.payload_hash and result.valid:
                attachment_hash = _hash_from_xml_bytes(document_type, xml_bytes)
                if attachment_hash != event.payload_hash:
                    result.warnings.append(
                        'Hash XML priloga ne odgovara payload_hash eventa — '
                        'provjerite da je prilog isti sadržaj koji je predan.',
                    )
            return result

        name = getattr(file_obj, 'name', '') or ''
        ext = '.' + name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        guessed, _ = mimetypes.guess_type(name)
        if ext not in _ALLOWED_NON_XML_EXTENSIONS and guessed not in {
            'application/pdf', 'image/png', 'image/jpeg', 'application/zip',
        }:
            return ValidationResult(
                valid=False,
                errors=[f'Nepodržana ekstenzija priloga: {ext or "n/a"}'],
            )
        return ValidationResult(valid=True)

    @staticmethod
    @transaction.atomic
    def create_event(
        document,
        *,
        destination: str,
        external_identifier: UUID | str,
        submitted_at: datetime,
        submitted_by,
        source: str,
        payload_hash: str | None = None,
        state: str = SubmissionEventState.SUBMITTED,
        version_confirmed: bool = True,
        confirmation_attachment=None,
    ) -> SubmissionEvent:
        """Create an append-only submission audit event for a tax document."""
        document = _assert_submission_document(document)

        if not version_confirmed:
            raise CreateSubmissionEventError(
                'Potvrdite da predajete istu verziju obrasca generiranog u ERP-u.',
            )

        if isinstance(external_identifier, str):
            external_identifier = UUID(external_identifier)

        resolved_hash = _resolve_payload_hash(document, payload_hash)
        content_type = ContentType.objects.get_for_model(document)
        tenant = document.tenant

        SubmissionEvent.all_objects.select_for_update().filter(
            tenant=tenant,
            content_type=content_type,
            object_id=document.pk,
        )

        if SubmissionEvent.all_objects.filter(
            tenant=tenant,
            destination=destination,
            external_identifier=external_identifier,
        ).exists():
            raise DuplicateExternalIdentifierError(
                f'Vanjski identifikator {external_identifier} već postoji '
                f'za odredište {destination}.',
            )

        submission_no = next_submission_no(document)
        supersedes = None
        if submission_no > 1:
            supersedes = (
                SubmissionEvent.all_objects.filter(
                    tenant=tenant,
                    content_type=content_type,
                    object_id=document.pk,
                )
                .order_by('-submission_no')
                .first()
            )

        event = SubmissionEvent.objects.create(
            tenant=tenant,
            content_type=content_type,
            object_id=document.pk,
            submission_no=submission_no,
            state=state,
            destination=destination,
            external_identifier=external_identifier,
            payload_hash=resolved_hash,
            submitted_at=submitted_at,
            submitted_by=submitted_by,
            source=source,
            confirmation_attachment=confirmation_attachment,
            supersedes_submission=supersedes,
        )

        SubmissionService._update_document_workflow(document, submitted_at=submitted_at)
        return event

    @staticmethod
    def _update_document_workflow(document, *, submitted_at: datetime) -> None:
        from accounting.models import PDVSReturn, VATReturn, ZPReturn

        if isinstance(document, VATReturn):
            period = VATPeriod.all_objects.select_for_update().get(pk=document.vat_period_id)
            if document.status == VATReturnStatus.GENERATED:
                document.status = VATReturnStatus.SUBMITTED
                document.save(update_fields=['status'])
            period.status = 'submitted'
            period.submitted_at = submitted_at
            period.save(update_fields=['status', 'submitted_at'])
        elif isinstance(document, (PDVSReturn, ZPReturn)):
            period = VATPeriod.all_objects.select_for_update().get(pk=document.vat_period_id)
            if period.status != 'submitted':
                period.status = 'submitted'
                period.submitted_at = submitted_at
                period.save(update_fields=['status', 'submitted_at'])

    @staticmethod
    @transaction.atomic
    def attach_confirmation(
        event: SubmissionEvent,
        file_obj,
        *,
        uploaded_by,
        reject_on_error: bool = True,
    ) -> SubmissionEvent:
        """Attach a confirmation file to an existing submission event (once)."""
        if event.confirmation_attachment:
            raise AttachConfirmationError('Potvrda predaje već postoji — nije dopušteno prepisivanje.')

        document = event.document
        if document is None:
            raise AttachConfirmationError('Dokument predaje nije pronađen.')

        validation = SubmissionService.validate(file_obj, document=document, event=event)
        if validation.errors:
            message = '; '.join(validation.errors)
            if reject_on_error:
                raise AttachConfirmationError(message)
            logger.warning('attach_confirmation validation errors: %s', message)

        for warning in validation.warnings:
            logger.warning('attach_confirmation: %s', warning)

        SubmissionEvent.all_objects.select_for_update().get(pk=event.pk)
        event.confirmation_attachment = file_obj
        event._allow_attachment_set = True  # noqa: SLF001
        event.save(update_fields=['confirmation_attachment'])
        return event

    @staticmethod
    @transaction.atomic
    def supersede(
        document,
        *,
        previous_event: SubmissionEvent,
        destination: str,
        external_identifier: UUID | str,
        submitted_at: datetime,
        submitted_by,
        source: str,
        payload_hash: str | None = None,
        version_confirmed: bool = True,
        confirmation_attachment=None,
    ) -> SubmissionEvent:
        """Create a correction submission linked to the previous event."""
        document = _assert_submission_document(document)
        current = SubmissionService.current_submission(document)
        if current is None or current.pk != previous_event.pk:
            raise SupersedeSubmissionError(
                'Ispravak je dopušten samo nad trenutno aktivnom predajom.',
            )

        return SubmissionService.create_event(
            document,
            destination=destination,
            external_identifier=external_identifier,
            submitted_at=submitted_at,
            submitted_by=submitted_by,
            source=source,
            payload_hash=payload_hash,
            version_confirmed=version_confirmed,
            confirmation_attachment=confirmation_attachment,
        )

    @staticmethod
    def transition_state(event: SubmissionEvent, new_state: str, *, transitioned_by) -> SubmissionEvent:
        """Change submission state through the allowed lifecycle transitions."""
        return transition_submission_state(event, new_state, transitioned_by=transitioned_by)
