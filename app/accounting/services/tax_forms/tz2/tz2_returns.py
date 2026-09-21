"""Create and persist ERP-generated TZ2Return drafts."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from accounting.models import TZ2Return
from accounting.services.tax_forms.pdv.build import accountant_for_tenant
from accounting.services.tax_forms.tz2.aggregate import Tz2BuildInput, alma_2026_input
from accounting.services.tax_forms.tz2.build import build_tz2_payload
from accounting.services.tax_forms.tz2.canonical import canonical_json, payload_hash, payload_to_dict
from accounting.services.tax_forms.tz2.mapping import ALMA_OIB
from accounting.services.tax_forms.tz2.payload import Tz2Payload
from accounting.services.tax_forms.tz2.render import render_tz2_xml
from accounting.services.tax_forms.tz2.validation import validate_tz2_payload, validate_tz2_xml
from accounting.services.tax_forms.tz2.versions import next_tz2_return_version
from settings.models import CompanySettings, ResponsiblePerson


class _StagedFileStorage:
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


def _storage_base_path(tenant, tax_year: int, version: int) -> str:
    return f'tz2_returns/{tenant.slug}/{tax_year}/v{version}/'


def get_latest_tz2_return(tenant, tax_year: int) -> TZ2Return | None:
    return (
        TZ2Return.all_objects.filter(tenant=tenant, tax_year=tax_year)
        .order_by('-version')
        .first()
    )


def build_input_from_snapshot(snapshot: dict, *, prepared_by: ResponsiblePerson | None) -> Tz2BuildInput:
    taxpayer = snapshot['taxpayer']
    preparer = snapshot.get('prepared_by') or {}
    first = (prepared_by.first_name if prepared_by else preparer.get('first_name')) or ''
    last = (prepared_by.last_name if prepared_by else preparer.get('last_name')) or ''
    return Tz2BuildInput(
        first_name=taxpayer['first_name'],
        last_name=taxpayer['last_name'],
        oib=taxpayer['oib'],
        municipality_code=taxpayer['municipality_code'],
        city=taxpayer['city'],
        street=taxpayer['street'],
        house_number=taxpayer['house_number'],
        prepared_by_first_name=first,
        prepared_by_last_name=last,
        room_beds=int(snapshot.get('room_beds') or 0),
        aux_beds=int(snapshot.get('aux_beds') or 0),
        camp_units=int(snapshot.get('camp_units') or 0),
        robinson_units=int(snapshot.get('robinson_units') or 0),
        opg_room_beds=int(snapshot.get('opg_room_beds') or 0),
        opg_aux_beds=int(snapshot.get('opg_aux_beds') or 0),
        opg_camp_units=int(snapshot.get('opg_camp_units') or 0),
        opg_robinson_units=int(snapshot.get('opg_robinson_units') or 0),
        room_bed_rate=Decimal(snapshot['room_bed_rate']) if snapshot.get('room_bed_rate') is not None else None,
        aux_bed_rate=Decimal(snapshot['aux_bed_rate']) if snapshot.get('aux_bed_rate') is not None else None,
        camp_rate=Decimal(snapshot['camp_rate']) if snapshot.get('camp_rate') is not None else None,
        robinson_rate=Decimal(snapshot['robinson_rate']) if snapshot.get('robinson_rate') is not None else None,
        opg_room_bed_rate=(
            Decimal(snapshot['opg_room_bed_rate']) if snapshot.get('opg_room_bed_rate') is not None else None
        ),
        opg_aux_bed_rate=(
            Decimal(snapshot['opg_aux_bed_rate']) if snapshot.get('opg_aux_bed_rate') is not None else None
        ),
        opg_camp_rate=Decimal(snapshot['opg_camp_rate']) if snapshot.get('opg_camp_rate') is not None else None,
        opg_robinson_rate=(
            Decimal(snapshot['opg_robinson_rate']) if snapshot.get('opg_robinson_rate') is not None else None
        ),
        discount_group_1=Decimal(snapshot.get('discount_group_1') or '0.00'),
        discount_group_2=Decimal(snapshot.get('discount_group_2') or '0.00'),
        discount_group_3=Decimal(snapshot.get('discount_group_3') or '0.00'),
        discount_group_4=Decimal(snapshot.get('discount_group_4') or '0.00'),
        payment_installments=bool(snapshot.get('payment_installments')),
        ep_receipts=Decimal(snapshot.get('ep_receipts') or '0.00'),
    )


def default_build_input(tenant, tax_year: int, *, prepared_by: ResponsiblePerson | None) -> Tz2BuildInput:
    settings = CompanySettings.all_objects.filter(tenant=tenant).first()
    first = (prepared_by.first_name if prepared_by else '') or ''
    last = (prepared_by.last_name if prepared_by else '') or ''
    oib = (settings.vat_number if settings else '') or ''
    if oib == ALMA_OIB and tax_year == 2026:
        return alma_2026_input(prepared_by_first_name=first or 'NA', prepared_by_last_name=last or 'NA')
    return Tz2BuildInput(
        first_name='',
        last_name='',
        oib=oib,
        municipality_code='',
        city=(settings.city if settings else '') or '',
        street=(settings.street if settings else '') or '',
        house_number=(settings.house_number if settings else '') or '',
        prepared_by_first_name=first,
        prepared_by_last_name=last,
    )


def payload_from_return(tz2_return: TZ2Return) -> Tz2Payload:
    person = tz2_return.prepared_by
    build_input = build_input_from_snapshot(tz2_return.payload_snapshot, prepared_by=person)
    return build_tz2_payload(tz2_return.tax_year, build_input)


@transaction.atomic
def create_tz2_return_draft(
    tenant,
    tax_year: int,
    *,
    build_input: Tz2BuildInput | None = None,
    prepared_by: ResponsiblePerson | None = None,
) -> TZ2Return:
    """Build, validate, hash, version and persist a new TZ2Return draft."""
    person = prepared_by or accountant_for_tenant(tenant)
    resolved = build_input or default_build_input(tenant, tax_year, prepared_by=person)
    if person is not None:
        resolved = Tz2BuildInput(
            **{
                **resolved.__dict__,
                'prepared_by_first_name': (person.first_name or resolved.prepared_by_first_name or 'NA').strip(),
                'prepared_by_last_name': (person.last_name or resolved.prepared_by_last_name or 'NA').strip(),
            }
        )
    version = next_tz2_return_version(tenant, tax_year)
    payload = build_tz2_payload(tax_year, resolved)
    validate_tz2_payload(payload)
    xml_bytes = render_tz2_xml(payload)
    validate_tz2_xml(xml_bytes)

    phash = payload_hash(payload)
    snapshot = payload_to_dict(payload)
    canonical = canonical_json(payload)
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
            unsigned_xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
            prepared_by=person,
        )
        staged.commit()
        return tz2_return
    except Exception:
        staged.rollback()
        raise
