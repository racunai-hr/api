"""Create and persist ERP-generated JOPPDReturn drafts."""

from __future__ import annotations

import hashlib

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from accounting.models import JOPPDReturn
from domains.tax.forms.joppd.build import build_joppd_payload
from domains.tax.forms.joppd.canonical import canonical_json, payload_hash, payload_to_dict
from domains.tax.forms.joppd.payload import JoppdPayload
from domains.tax.forms.joppd.render import render_joppd_xml
from domains.tax.forms.joppd.validation import validate_joppd_payload
from domains.tax.forms.joppd.versions import next_joppd_return_version


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


def _storage_base_path(tenant, year: int, month: int, version: int) -> str:
    return f'joppd_returns/{tenant.slug}/{year}/{month:02d}/v{version}/'


@transaction.atomic
def create_joppd_return_draft(tenant, year: int, month: int, *, prepared_by=None) -> JOPPDReturn:
    """Build, validate, hash, version and persist a new JOPPDReturn draft atomically."""
    version = next_joppd_return_version(tenant, year, month)
    payload: JoppdPayload = build_joppd_payload(tenant, year, month)
    validate_joppd_payload(payload)
    xml_bytes = render_joppd_xml(payload)
    from domains.tax.forms.joppd.validation import validate_joppd_xml
    validate_joppd_xml(xml_bytes)

    phash = payload_hash(payload)
    snapshot = payload_to_dict(payload)
    canonical = canonical_json(payload)
    base_path = _storage_base_path(tenant, year, month, version)

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
        joppd_return = JOPPDReturn.objects.create(
            tenant=tenant,
            period_year=year,
            period_month=month,
            version=version,
            schema_version=payload.schema_version,
            mapping_version=payload.mapping_version,
            payload_snapshot=snapshot,
            payload_hash=phash,
            payload_json=payload_path,
            xml_unsigned=xml_path,
            unsigned_xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
            prepared_by=prepared_by,
        )
        staged.commit()
        return joppd_return
    except Exception:
        staged.rollback()
        raise


def get_latest_joppd_return(tenant, year: int, month: int) -> JOPPDReturn | None:
    return (
        JOPPDReturn.all_objects.filter(tenant=tenant, period_year=year, period_month=month)
        .order_by('-version')
        .first()
    )


def get_or_create_joppd_return(tenant, year: int, month: int, *, prepared_by=None) -> JOPPDReturn:
    existing = get_latest_joppd_return(tenant, year, month)
    if existing is not None:
        return existing
    return create_joppd_return_draft(tenant, year, month, prepared_by=prepared_by)
