"""TZ2 year read-model."""

from __future__ import annotations

from accounting.services.submission.events import get_submission_events
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.tz2.mapping import rates_for_year
from accounting.services.tax_forms.tz2.payload import Tz2Payload
from accounting.services.tax_forms.tz2.tz2_returns import (
    default_build_input,
    get_latest_tz2_return,
    payload_from_return,
)
from accounting.services.tax_forms.pdv.build import accountant_for_tenant
from domains.tax.read.dto import money2, pdv_s_submission_dto


def tz2_payload_dto(payload: Tz2Payload) -> dict:
    rates = rates_for_year(payload.period_from.year, mapping_version=payload.mapping_version)
    return {
        'schema_version': payload.schema_version,
        'mapping_version': payload.mapping_version,
        'period_from': payload.period_from.isoformat(),
        'period_to': payload.period_to.isoformat(),
        'rates': {key: money2(value) for key, value in rates.items()},
        'taxpayer': {
            'first_name': payload.taxpayer.first_name,
            'last_name': payload.taxpayer.last_name,
            'oib': payload.taxpayer.oib,
            'municipality_code': payload.taxpayer.municipality_code,
            'city': payload.taxpayer.city,
            'street': payload.taxpayer.street,
            'house_number': payload.taxpayer.house_number,
        },
        'room_beds': payload.room_beds,
        'aux_beds': payload.aux_beds,
        'camp_units': payload.camp_units,
        'robinson_units': payload.robinson_units,
        'opg_room_beds': payload.opg_room_beds,
        'opg_aux_beds': payload.opg_aux_beds,
        'opg_camp_units': payload.opg_camp_units,
        'opg_robinson_units': payload.opg_robinson_units,
        'room_bed_rate': money2(payload.room_bed_rate),
        'aux_bed_rate': money2(payload.aux_bed_rate),
        'camp_rate': money2(payload.camp_rate),
        'robinson_rate': money2(payload.robinson_rate),
        'opg_room_bed_rate': money2(payload.opg_room_bed_rate),
        'opg_aux_bed_rate': money2(payload.opg_aux_bed_rate),
        'opg_camp_rate': money2(payload.opg_camp_rate),
        'opg_robinson_rate': money2(payload.opg_robinson_rate),
        'room_bed_total': money2(payload.room_bed_total),
        'aux_bed_total': money2(payload.aux_bed_total),
        'total_assessed': money2(payload.total_assessed),
        'discount_group_1': money2(payload.discount_group_1),
        'discount_group_2': money2(payload.discount_group_2),
        'discount_group_3': money2(payload.discount_group_3),
        'discount_group_4': money2(payload.discount_group_4),
        'total_discount': money2(payload.total_discount),
        'amount_after_discount': money2(payload.amount_after_discount),
        'payment_installments': payload.payment_installments,
        'lump_sum_flag': payload.lump_sum_flag,
        'lump_sum_amount': money2(payload.lump_sum_amount),
        'installment_flag': payload.installment_flag,
        'installment_amount': money2(payload.installment_amount),
        'ep_receipts': money2(payload.ep_receipts),
    }


def tz2_year_dto(tenant, tax_year: int) -> dict:
    tz2_return = get_latest_tz2_return(tenant, tax_year)
    if tz2_return is None:
        person = accountant_for_tenant(tenant)
        build_input = default_build_input(tenant, tax_year, prepared_by=person)
        from accounting.services.tax_forms.tz2.build import build_tz2_payload

        payload = build_tz2_payload(tax_year, build_input)
        body = tz2_payload_dto(payload)
        body.update({
            'tax_year': tax_year,
            'version': None,
            'persisted': False,
            'event_uuid': None,
            'current_submission': None,
            'submissions': [],
        })
        return body

    payload = payload_from_return(tz2_return)
    submissions = [pdv_s_submission_dto(event) for event in get_submission_events(tz2_return)]
    current = SubmissionService.current_submission(tz2_return)
    current_submission = pdv_s_submission_dto(current) if current is not None else None
    body = tz2_payload_dto(payload)
    body.update({
        'tax_year': tax_year,
        'version': tz2_return.version,
        'persisted': True,
        'event_uuid': current_submission['event_uuid'] if current_submission else None,
        'current_submission': current_submission,
        'submissions': submissions,
        'payload_hash': tz2_return.payload_hash,
    })
    return body
