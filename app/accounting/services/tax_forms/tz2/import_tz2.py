"""Import an already-submitted Obrazac TZ 2 from ePorezna XML + PDF evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from django.core.files.base import ContentFile
from django.db import transaction

from accounting.models import (
    SubmissionDestination,
    SubmissionEvent,
    SubmissionSource,
    TZ2Return,
)
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.build import accountant_for_tenant
from accounting.services.tax_forms.tz2.canonical import canonical_json, payload_hash, payload_to_dict
from accounting.services.tax_forms.tz2.parse import parse_tz2_xml, parse_tz2_xml_meta
from accounting.services.tax_forms.tz2.tz2_returns import (
    _StagedFileStorage,
    _storage_base_path,
    next_tz2_return_version,
)
from accounting.services.tax_forms.tz2.validation import (
    Tz2SchemaValidationError,
    Tz2ValidationError,
    validate_tz2_payload,
    validate_tz2_xml,
)
from settings.models import CompanySettings

_ZAGREB = ZoneInfo('Europe/Zagreb')
_FIXTURE_DIR = (
    Path(__file__).resolve().parents[3] / 'tests' / 'fixtures' / 'tax' / 'tz2' / 'carolina_2026'
)
CAROLINA_2026_XML = _FIXTURE_DIR / 'submitted.xml'
CAROLINA_2026_SUBMITTED_PDF = _FIXTURE_DIR / 'submitted.pdf'
CAROLINA_2026_PROCESSED_PDF = _FIXTURE_DIR / 'processed.pdf'


class Tz2ImportError(Exception):
    """Raised when a historical TZ 2 import cannot be recorded."""


@dataclass(frozen=True)
class Tz2ImportResult:
    tz2_return: TZ2Return
    submitted_event: SubmissionEvent
    processed_event: SubmissionEvent
    created: bool


def processed_external_identifier(identifikator: UUID) -> UUID:
    """Deterministic UUID for the processed-PDF event (portal Identifikator is unique)."""
    return uuid5(identifikator, 'processed')


def _aware_zagreb(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_ZAGREB)
    return value


def _named_file(name: str, data: bytes) -> ContentFile:
    return ContentFile(data, name=name)


def _existing_event(tenant, identifikator: UUID) -> SubmissionEvent | None:
    return (
        SubmissionEvent.all_objects.filter(
            tenant=tenant,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=identifikator,
        )
        .select_related('content_type')
        .first()
    )


def _persist_imported_return(tenant, tax_year: int, xml_bytes: bytes, payload) -> TZ2Return:
    xml_sha = hashlib.sha256(xml_bytes).hexdigest()
    existing = (
        TZ2Return.all_objects.filter(
            tenant=tenant,
            tax_year=tax_year,
            unsigned_xml_sha256=xml_sha,
        )
        .order_by('-version')
        .first()
    )
    if existing is not None:
        return existing

    person = accountant_for_tenant(tenant)
    version = next_tz2_return_version(tenant, tax_year)
    snapshot = payload_to_dict(payload)
    canonical = canonical_json(payload)
    phash = payload_hash(payload)
    base_path = _storage_base_path(tenant, tax_year, version)
    staged = _StagedFileStorage()
    try:
        payload_path = staged.save(
            f'{base_path}payload.json',
            ContentFile(canonical.encode('utf-8')),
        )
        xml_path = staged.save(
            f'{base_path}unsigned.xml',
            ContentFile(xml_bytes),
        )
        submitted_path = staged.save(
            f'{base_path}submitted.xml',
            ContentFile(xml_bytes),
        )
        tz2_return = TZ2Return.objects.create(
            tenant=tenant,
            tax_year=tax_year,
            version=version,
            schema_version=payload.schema_version,
            mapping_version=payload.mapping_version,
            payload_snapshot=snapshot,
            payload_hash=phash,
            payload_json=payload_path,
            xml_unsigned=xml_path,
            xml_submitted=submitted_path,
            unsigned_xml_sha256=xml_sha,
            prepared_by=person,
        )
        staged.commit()
        return tz2_return
    except Exception:
        staged.rollback()
        raise


@transaction.atomic
def import_tz2_from_xml(
    tenant,
    tax_year: int,
    xml_bytes: bytes,
    *,
    submitted_pdf_bytes: bytes,
    processed_pdf_bytes: bytes,
    submitted_by,
) -> Tz2ImportResult:
    """Parse portal XML, persist original bytes, then record submitted + processed PDF events.

    Does not lock VATPeriod. Does not re-render XML (keeps the portal Identifikator).
    """
    try:
        validate_tz2_xml(xml_bytes)
    except Tz2SchemaValidationError as exc:
        raise Tz2ImportError(str(exc)) from exc

    try:
        payload = parse_tz2_xml(xml_bytes)
        meta = parse_tz2_xml_meta(xml_bytes)
        validate_tz2_payload(payload)
    except (ValueError, Tz2ValidationError) as exc:
        raise Tz2ImportError(str(exc)) from exc

    if payload.period_from.year != tax_year or payload.period_to.year != tax_year:
        raise Tz2ImportError(
            f'XML razdoblje {payload.period_from.year}–{payload.period_to.year} '
            f'ne odgovara godini {tax_year}.'
        )

    settings = CompanySettings.all_objects.filter(tenant=tenant).first()
    if settings and settings.vat_number and settings.vat_number != payload.taxpayer.oib:
        raise Tz2ImportError(
            f'OIB u XML-u ({payload.taxpayer.oib}) ne odgovara OIB-u tvrtke '
            f'({settings.vat_number}).'
        )

    submitted_at = _aware_zagreb(meta.datum)
    processed_id = processed_external_identifier(meta.identifikator)

    existing = _existing_event(tenant, meta.identifikator)
    if existing is not None:
        document = existing.document
        if not isinstance(document, TZ2Return) or document.tax_year != tax_year:
            raise Tz2ImportError(
                f'Identifikator {meta.identifikator} već postoji na drugom dokumentu.'
            )
        processed = _existing_event(tenant, processed_id)
        if processed is None:
            processed = SubmissionService.supersede(
                document,
                previous_event=existing,
                destination=SubmissionDestination.EPOREZNA,
                external_identifier=processed_id,
                submitted_at=submitted_at,
                submitted_by=submitted_by,
                source=SubmissionSource.IMPORT,
                confirmation_attachment=_named_file('tz2_processed.pdf', processed_pdf_bytes),
            )
        return Tz2ImportResult(
            tz2_return=document,
            submitted_event=existing,
            processed_event=processed,
            created=False,
        )

    tz2_return = _persist_imported_return(tenant, tax_year, xml_bytes, payload)
    submitted = SubmissionService.create_event(
        tz2_return,
        destination=SubmissionDestination.EPOREZNA,
        external_identifier=meta.identifikator,
        submitted_at=submitted_at,
        submitted_by=submitted_by,
        source=SubmissionSource.IMPORT,
        confirmation_attachment=_named_file('tz2_submitted.pdf', submitted_pdf_bytes),
    )
    processed = SubmissionService.supersede(
        tz2_return,
        previous_event=submitted,
        destination=SubmissionDestination.EPOREZNA,
        external_identifier=processed_id,
        submitted_at=submitted_at,
        submitted_by=submitted_by,
        source=SubmissionSource.IMPORT,
        confirmation_attachment=_named_file('tz2_processed.pdf', processed_pdf_bytes),
    )
    return Tz2ImportResult(
        tz2_return=tz2_return,
        submitted_event=submitted,
        processed_event=processed,
        created=True,
    )
