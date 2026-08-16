"""Create and persist ERP-generated VATReturn drafts."""

from __future__ import annotations

import hashlib

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from accounting.models import VATPeriod, VATReturn, VATReturnSource, VATReturnStatus
from accounting.services.tax_forms.pdv.build import accountant_for_tenant, build_pdv_payload
from accounting.services.tax_forms.pdv.canonical import canonical_json, payload_hash, payload_to_dict
from accounting.services.tax_forms.pdv.payload import PdvFormHeader
from accounting.services.tax_forms.pdv.render import render_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.validation import validate_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.versions import next_vat_return_version
from settings.models import CompanySettings, ResponsiblePerson


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
        f'vat_returns/{period.tenant.slug}/'
        f'{period.year}/{period.month:02d}/v{version}/'
    )


def _resolve_form_header(
    period: VATPeriod,
    prepared_by: ResponsiblePerson | None,
) -> tuple[PdvFormHeader, ResponsiblePerson | None]:
    settings = CompanySettings.all_objects.filter(tenant=period.tenant).first()
    if settings is None:
        raise ValueError(f'CompanySettings missing for tenant {period.tenant_id}')

    person = prepared_by or accountant_for_tenant(period.tenant)
    tax_office_code = settings.tax_office.code if settings.tax_office_id else ''
    if person is not None:
        first_name = person.first_name
        last_name = person.last_name
    else:
        first_name = 'NA'
        last_name = 'NA'

    return (
        PdvFormHeader(
            tax_office_code=tax_office_code,
            prepared_by_first_name=first_name,
            prepared_by_last_name=last_name,
        ),
        person,
    )


@transaction.atomic
def create_vat_return_draft(
    period: VATPeriod,
    *,
    prepared_by: ResponsiblePerson | None = None,
) -> VATReturn:
    """Build, validate, hash, version and persist a new VATReturn draft atomically."""
    version = next_vat_return_version(period)
    payload = build_pdv_payload(period)
    header, preparer = _resolve_form_header(period, prepared_by)
    xml_bytes = render_pdv_obrazac_xml(payload, header=header)
    validate_pdv_obrazac_xml(xml_bytes)

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
        vat_return = VATReturn.objects.create(
            tenant=period.tenant,
            vat_period=period,
            version=version,
            status=VATReturnStatus.GENERATED,
            source=VATReturnSource.ERP_GENERATED,
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
        return vat_return
    except Exception:
        staged.rollback()
        raise


_RESYNC_ALLOWED_STATUSES = frozenset({
    VATReturnStatus.DRAFT,
    VATReturnStatus.GENERATED,
})


@transaction.atomic
def resync_unsigned_xml_from_payload(vat_return: VATReturn) -> VATReturn:
    """Re-render unsigned.xml from existing payload.json without changing version or payload."""
    if vat_return.status not in _RESYNC_ALLOWED_STATUSES:
        raise ValueError(
            f'Resync nije dopušten za status "{vat_return.get_status_display()}".'
        )
    if vat_return.source != VATReturnSource.ERP_GENERATED:
        raise ValueError('Resync je dopušten samo za ERP-generirane obrasce.')

    from accounting.services.tax_forms.pdv.import_return import load_payload_from_file

    payload = load_payload_from_file(vat_return)
    header, _ = _resolve_form_header(vat_return.vat_period, vat_return.prepared_by)
    xml_bytes = render_pdv_obrazac_xml(payload, header=header)
    validate_pdv_obrazac_xml(xml_bytes)

    old_path = vat_return.xml_unsigned.name if vat_return.xml_unsigned else None
    base_path = _storage_base_path(vat_return.vat_period, vat_return.version)
    if old_path and default_storage.exists(old_path):
        default_storage.delete(old_path)

    xml_path = default_storage.save(
        f'{base_path}unsigned.xml',
        ContentFile(xml_bytes),
    )
    vat_return.xml_unsigned = xml_path
    vat_return.unsigned_xml_sha256 = hashlib.sha256(xml_bytes).hexdigest()
    vat_return.save(update_fields=['xml_unsigned', 'unsigned_xml_sha256'])
    return vat_return
