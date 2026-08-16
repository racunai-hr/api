"""Import signed Obrazac PDV XML — single entry point for admin, CLI and API."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from lxml import etree

from accounting.models import (
    VATPeriod,
    VATReturn,
    VATReturnSource,
    VATReturnStatus,
)
from accounting.services.tax_forms.pdv.canonical import canonical_json, payload_hash, payload_to_dict
from accounting.services.tax_forms.pdv.diff import PayloadMismatchError, compare_pdv_payload_fields
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.payload import (
    PdvFieldPair,
    PdvMarginPair,
    PdvPayload,
    TaxpayerInfo,
    freeze_fields,
)
from accounting.services.tax_forms.pdv.validation import validate_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.vat_returns import _StagedFileStorage, _storage_base_path
from accounting.services.tax_forms.pdv.versions import next_vat_return_version

_METADATA_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'
_SIGNATURE_NS = 'http://www.w3.org/2000/09/xmldsig#'


def payload_from_snapshot(snapshot: dict) -> PdvPayload:
    """Reconstruct a frozen PdvPayload from a VATReturn.payload_snapshot dict."""
    fields: dict[str, PdvFieldPair | PdvMarginPair | Decimal | bool] = {}
    for code, value in snapshot['fields'].items():
        if isinstance(value, dict):
            if 'nabavna_vrijednost' in value:
                fields[code] = PdvMarginPair(
                    Decimal(value['nabavna_vrijednost']),
                    Decimal(value['prodajna_vrijednost']),
                )
            else:
                fields[code] = PdvFieldPair(
                    Decimal(value['vrijednost']),
                    Decimal(value['porez']),
                )
        elif isinstance(value, bool):
            fields[code] = value
        else:
            fields[code] = Decimal(value)

    taxpayer_data = snapshot['taxpayer']
    return PdvPayload(
        schema_version=snapshot['schema_version'],
        mapping_version=snapshot['mapping_version'],
        period_from=date.fromisoformat(snapshot['period_from']),
        period_to=date.fromisoformat(snapshot['period_to']),
        taxpayer=TaxpayerInfo(
            oib=taxpayer_data['oib'],
            name=taxpayer_data['name'],
            street=taxpayer_data['street'],
            house_number=taxpayer_data['house_number'],
            city=taxpayer_data['city'],
            postal_code=taxpayer_data.get('postal_code', ''),
        ),
        fields=freeze_fields(fields),
    )


def load_payload_from_file(vat_return: VATReturn) -> PdvPayload:
    """Load PdvPayload from stored payload.json."""
    if not vat_return.payload_json:
        raise FileNotFoundError('payload.json nije postavljen')
    if not default_storage.exists(vat_return.payload_json.name):
        raise FileNotFoundError(f'payload.json ne postoji: {vat_return.payload_json.name}')
    raw = default_storage.open(vat_return.payload_json.name).read().decode('utf-8')
    return payload_from_snapshot(json.loads(raw))


def _extract_eporezna_identifier(xml_bytes: bytes) -> UUID | None:
    root = etree.fromstring(xml_bytes)
    identifier = root.find(f'.//{{{_METADATA_NS}}}Identifikator')
    if identifier is None or not identifier.text:
        return None
    try:
        return UUID(identifier.text.strip())
    except ValueError:
        return None


def _assert_period_matches_payload(period: VATPeriod, parsed: PdvPayload) -> None:
    if parsed.period_from.year != period.year or parsed.period_from.month != period.month:
        raise ValueError(
            f'XML razdoblje {parsed.period_from:%m/%Y} ne odgovara PDV razdoblju '
            f'{period.month:02d}/{period.year}.'
        )


def _mark_period_submitted(period: VATPeriod, submitted_at=None) -> None:
    period.status = 'submitted'
    period.submitted_at = submitted_at or timezone.now()
    period.save(update_fields=['status', 'submitted_at'])


def _attach_submitted_xml(
    vat_return: VATReturn,
    xml_bytes: bytes,
    imported_hash: str,
    parsed: PdvPayload,
) -> VATReturn:
    base_path = _storage_base_path(vat_return.vat_period, vat_return.version)
    xml_path = default_storage.save(
        f'{base_path}submitted.xml',
        ContentFile(xml_bytes),
    )
    vat_return.xml_submitted = xml_path
    vat_return.xml_sha256 = hashlib.sha256(xml_bytes).hexdigest()
    vat_return.payload_hash = imported_hash
    vat_return.payload_snapshot = payload_to_dict(parsed)
    return vat_return


def _mark_submitted(vat_return: VATReturn, parsed: PdvPayload, xml_bytes: bytes) -> None:
    now = timezone.now()
    vat_return.status = VATReturnStatus.SUBMITTED
    vat_return.save(
        update_fields=[
            'status',
            'xml_submitted',
            'xml_sha256',
            'payload_hash',
            'payload_snapshot',
        ],
    )
    _mark_period_submitted(vat_return.vat_period, submitted_at=now)
    xml_doc_uuid = _extract_eporezna_identifier(xml_bytes)
    if xml_doc_uuid is not None:
        logger = __import__('logging').getLogger(__name__)
        logger.info(
            'import_signed_vat_return: XML Metapodaci Identifikator=%s (ne koristi se kao portal UUID)',
            xml_doc_uuid,
        )


def _create_imported_return(
    period: VATPeriod,
    xml_bytes: bytes,
    parsed: PdvPayload,
    imported_hash: str,
    *,
    source: str,
) -> VATReturn:
    version = next_vat_return_version(period)
    snapshot = payload_to_dict(parsed)
    canonical = canonical_json(parsed)
    base_path = _storage_base_path(period, version)

    staged = _StagedFileStorage()
    try:
        payload_path = staged.save(
            f'{base_path}payload.json',
            ContentFile(canonical.encode('utf-8')),
        )
        xml_path = staged.save(
            f'{base_path}submitted.xml',
            ContentFile(xml_bytes),
        )
        vat_return = VATReturn.objects.create(
            tenant=period.tenant,
            vat_period=period,
            version=version,
            status=VATReturnStatus.IMPORTED,
            source=source,
            schema_version=parsed.schema_version,
            mapping_version=parsed.mapping_version,
            payload_snapshot=snapshot,
            payload_hash=imported_hash,
            payload_json=payload_path,
            xml_submitted=xml_path,
            xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
        )
        staged.commit()
        submitted_at = timezone.now()
        _mark_period_submitted(period, submitted_at=submitted_at)
        return vat_return
    except Exception:
        staged.rollback()
        raise


@transaction.atomic
def import_signed_vat_return(
    period: VATPeriod,
    xml_bytes: bytes,
    *,
    vat_return: VATReturn | None = None,
    source: str = VATReturnSource.MANUAL_UPLOAD,
) -> VATReturn:
    """Parse, validate and persist a signed Obrazac PDV XML."""
    parsed = parse_pdv_obrazac_xml(xml_bytes)
    validate_pdv_obrazac_xml(xml_bytes, signed=True)
    _assert_period_matches_payload(period, parsed)
    imported_hash = payload_hash(parsed)

    if vat_return is not None:
        if imported_hash != vat_return.payload_hash:
            draft_payload = payload_from_snapshot(vat_return.payload_snapshot)
            differences = compare_pdv_payload_fields(draft_payload, parsed)
            if differences:
                raise PayloadMismatchError(differences)
        vat_return = _attach_submitted_xml(vat_return, xml_bytes, imported_hash, parsed)
        _mark_submitted(vat_return, parsed, xml_bytes)
        return vat_return

    return _create_imported_return(
        period,
        xml_bytes,
        parsed,
        imported_hash,
        source=source,
    )
