"""Create and persist ERP-generated ZPReturn drafts."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from accounting.models import VATPeriod, ZPReturn
from accounting.services.tax_forms.pdv.build import accountant_for_tenant
from accounting.services.tax_forms.zp.build import build_zp_payload
from accounting.services.tax_forms.zp.canonical import canonical_json, payload_hash, payload_to_dict
from accounting.services.tax_forms.zp.payload import ZpPayload, ZpPreparedBy
from accounting.services.tax_forms.zp.render import render_zp_xml
from accounting.services.tax_forms.zp.validation import validate_zp_payload, validate_zp_xml
from accounting.services.tax_forms.zp.versions import next_zp_return_version
from settings.models import ResponsiblePerson


class _StagedFileStorage:
    """Track saved files so they can be removed if the DB transaction fails."""

    def __init__(self) -> None:
        self._paths: list[str] = []
        self._committed = False

    def save(self, name: str, content: ContentFile) -> str:
        path = default_storage.save(name, content)
        self._paths.append(path)
        return path

    def commit(self) -> None:
        self._committed = True

    def rollback(self) -> None:
        if self._committed:
            return
        for path in self._paths:
            if default_storage.exists(path):
                default_storage.delete(path)


def _storage_base_path(period: VATPeriod, version: int) -> str:
    return (
        f'zp_returns/{period.tenant.slug}/'
        f'{period.year}/{period.month:02d}/v{version}/'
    )


def _resolve_payload(
    period: VATPeriod,
    prepared_by: ResponsiblePerson | None,
) -> tuple[ZpPayload, ResponsiblePerson | None]:
    payload = build_zp_payload(period)
    person = prepared_by or accountant_for_tenant(period.tenant)
    if person is not None:
        payload = replace(
            payload,
            prepared_by=ZpPreparedBy(
                first_name=person.first_name,
                last_name=person.last_name,
                email=person.email or '',
                phone=person.phone or '',
            ),
        )
    return payload, person


@transaction.atomic
def create_zp_return_draft(
    period: VATPeriod,
    *,
    prepared_by: ResponsiblePerson | None = None,
) -> ZPReturn:
    """Build, validate, hash, version and persist a new ZPReturn draft atomically."""
    version = next_zp_return_version(period)
    payload, preparer = _resolve_payload(period, prepared_by)
    validate_zp_payload(payload)
    xml_bytes = render_zp_xml(payload)
    validate_zp_xml(xml_bytes)

    phash = payload_hash(payload)
    snapshot = payload_to_dict(payload)
    canonical = canonical_json(payload)
    base_path = _storage_base_path(period, version)

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
        zp_return = ZPReturn.objects.create(
            tenant=period.tenant,
            vat_period=period,
            version=version,
            schema_version=payload.schema_version,
            mapping_version=payload.mapping_version,
            payload_snapshot=snapshot,
            payload_hash=phash,
            payload_json=payload_path,
            xml_unsigned=xml_path,
            unsigned_xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
            prepared_by=preparer,
        )
        staged.commit()
        return zp_return
    except Exception:
        staged.rollback()
        raise
