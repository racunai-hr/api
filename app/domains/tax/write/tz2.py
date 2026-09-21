"""TZ2 write adapters — draft, XML, submit. Does not touch frozen SubmissionService."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from accounting.models import TZ2Return
from accounting.services.submission.exceptions import (
    CreateSubmissionEventError,
    DuplicateExternalIdentifierError,
)
from accounting.services.tax_forms.pdv.build import accountant_for_tenant
from accounting.services.tax_forms.tz2.aggregate import Tz2BuildInput
from accounting.services.tax_forms.tz2.submit import MarkTz2SubmittedError, mark_tz2_submitted
from accounting.services.tax_forms.tz2.tz2_returns import (
    create_tz2_return_draft,
    default_build_input,
    get_latest_tz2_return,
    payload_from_return,
)
from accounting.services.tax_forms.tz2.validation import Tz2ValidationError
from domains.tax.write.service import TaxBadRequest, TaxConflict, TaxNotFound, _submission_dto


def parse_tax_year(raw: str) -> int:
    try:
        year = int(raw)
    except (TypeError, ValueError) as exc:
        raise TaxNotFound() from exc
    if year < 2000 or year > 2100:
        raise TaxNotFound()
    return year


def _optional_decimal(data: dict, key: str):
    raw = data.get(key)
    if raw in (None, ''):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise TaxBadRequest(f'{key} mora biti broj.') from exc


def _int_field(data: dict, key: str, default: int = 0) -> int:
    raw = data.get(key, default)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise TaxBadRequest(f'{key} mora biti cijeli broj.') from exc


def build_input_from_request(data: dict, *, tenant, prepared_by) -> Tz2BuildInput:
    first = (prepared_by.first_name if prepared_by else '') or ''
    last = (prepared_by.last_name if prepared_by else '') or ''
    return Tz2BuildInput(
        first_name=str(data.get('first_name') or '').strip(),
        last_name=str(data.get('last_name') or '').strip(),
        oib=str(data.get('oib') or '').strip(),
        municipality_code=str(data.get('municipality_code') or '').strip(),
        city=str(data.get('city') or '').strip(),
        street=str(data.get('street') or '').strip(),
        house_number=str(data.get('house_number') or '').strip(),
        prepared_by_first_name=first,
        prepared_by_last_name=last,
        room_beds=_int_field(data, 'room_beds'),
        aux_beds=_int_field(data, 'aux_beds'),
        camp_units=_int_field(data, 'camp_units'),
        robinson_units=_int_field(data, 'robinson_units'),
        opg_room_beds=_int_field(data, 'opg_room_beds'),
        opg_aux_beds=_int_field(data, 'opg_aux_beds'),
        opg_camp_units=_int_field(data, 'opg_camp_units'),
        opg_robinson_units=_int_field(data, 'opg_robinson_units'),
        room_bed_rate=_optional_decimal(data, 'room_bed_rate'),
        aux_bed_rate=_optional_decimal(data, 'aux_bed_rate'),
        camp_rate=_optional_decimal(data, 'camp_rate'),
        robinson_rate=_optional_decimal(data, 'robinson_rate'),
        opg_room_bed_rate=_optional_decimal(data, 'opg_room_bed_rate'),
        opg_aux_bed_rate=_optional_decimal(data, 'opg_aux_bed_rate'),
        opg_camp_rate=_optional_decimal(data, 'opg_camp_rate'),
        opg_robinson_rate=_optional_decimal(data, 'opg_robinson_rate'),
        discount_group_1=_optional_decimal(data, 'discount_group_1') or Decimal('0.00'),
        discount_group_2=_optional_decimal(data, 'discount_group_2') or Decimal('0.00'),
        discount_group_3=_optional_decimal(data, 'discount_group_3') or Decimal('0.00'),
        discount_group_4=_optional_decimal(data, 'discount_group_4') or Decimal('0.00'),
        payment_installments=bool(data.get('payment_installments')),
        ep_receipts=_optional_decimal(data, 'ep_receipts') or Decimal('0.00'),
    )


def save_tz2_year(tenant, tax_year: int, data: dict) -> TZ2Return:
    person = accountant_for_tenant(tenant)
    build_input = build_input_from_request(data, tenant=tenant, prepared_by=person)
    try:
        return create_tz2_return_draft(
            tenant,
            tax_year,
            build_input=build_input,
            prepared_by=person,
        )
    except Tz2ValidationError as exc:
        raise TaxBadRequest(str(exc)) from exc


def tz2_xml_bytes(tenant, tax_year: int) -> tuple[bytes, str]:
    tz2_return = get_latest_tz2_return(tenant, tax_year)
    if tz2_return is not None:
        if tz2_return.xml_unsigned:
            xml_bytes = tz2_return.xml_unsigned.read()
            tz2_return.xml_unsigned.seek(0)
        else:
            from accounting.services.tax_forms.tz2.render import render_tz2_xml

            xml_bytes = render_tz2_xml(payload_from_return(tz2_return))
        payload = payload_from_return(tz2_return)
        filename = f'TZ2_{payload.taxpayer.oib}_{tax_year}.xml'
        return xml_bytes, filename

    person = accountant_for_tenant(tenant)
    build_input = default_build_input(tenant, tax_year, prepared_by=person)
    from accounting.services.tax_forms.tz2.build import build_tz2_payload
    from accounting.services.tax_forms.tz2.render import render_tz2_xml
    from accounting.services.tax_forms.tz2.validation import Tz2ValidationError, validate_tz2_payload, validate_tz2_xml

    try:
        payload = build_tz2_payload(tax_year, build_input)
        validate_tz2_payload(payload)
        xml_bytes = render_tz2_xml(payload)
        validate_tz2_xml(xml_bytes)
    except Tz2ValidationError as exc:
        raise TaxBadRequest(str(exc)) from exc
    filename = f'TZ2_{payload.taxpayer.oib}_{tax_year}.xml'
    return xml_bytes, filename


def submit_tz2_year(
    tenant,
    tax_year: int,
    *,
    user,
    eporezna_identifier: UUID,
    submitted_at: datetime,
) -> dict:
    tz2_return = get_latest_tz2_return(tenant, tax_year)
    if tz2_return is None:
        raise TaxNotFound()
    try:
        mark_tz2_submitted(
            tz2_return,
            submitted_at=submitted_at,
            eporezna_identifier=eporezna_identifier,
            submitted_by=user,
            version_confirmed=True,
        )
    except (MarkTz2SubmittedError, DuplicateExternalIdentifierError, CreateSubmissionEventError) as exc:
        raise TaxConflict(str(exc)) from exc
    from accounting.services.submission.service import SubmissionService

    event = SubmissionService.current_submission(tz2_return)
    return _submission_dto(event)
