"""Canonical JSON and payload hash for TZ 2."""

from __future__ import annotations

import hashlib
import json

from accounting.services.tax_forms.tz2.payload import Tz2Payload, money


def payload_to_dict(payload: Tz2Payload) -> dict:
    return {
        'schema_version': payload.schema_version,
        'mapping_version': payload.mapping_version,
        'period_from': payload.period_from.isoformat(),
        'period_to': payload.period_to.isoformat(),
        'taxpayer': {
            'first_name': payload.taxpayer.first_name,
            'last_name': payload.taxpayer.last_name,
            'oib': payload.taxpayer.oib,
            'municipality_code': payload.taxpayer.municipality_code,
            'city': payload.taxpayer.city,
            'street': payload.taxpayer.street,
            'house_number': payload.taxpayer.house_number,
        },
        'prepared_by': {
            'first_name': payload.prepared_by.first_name,
            'last_name': payload.prepared_by.last_name,
        },
        'room_beds': payload.room_beds,
        'aux_beds': payload.aux_beds,
        'camp_units': payload.camp_units,
        'robinson_units': payload.robinson_units,
        'opg_room_beds': payload.opg_room_beds,
        'opg_aux_beds': payload.opg_aux_beds,
        'opg_camp_units': payload.opg_camp_units,
        'opg_robinson_units': payload.opg_robinson_units,
        'room_bed_rate': str(money(payload.room_bed_rate)),
        'aux_bed_rate': str(money(payload.aux_bed_rate)),
        'camp_rate': str(money(payload.camp_rate)),
        'robinson_rate': str(money(payload.robinson_rate)),
        'opg_room_bed_rate': str(money(payload.opg_room_bed_rate)),
        'opg_aux_bed_rate': str(money(payload.opg_aux_bed_rate)),
        'opg_camp_rate': str(money(payload.opg_camp_rate)),
        'opg_robinson_rate': str(money(payload.opg_robinson_rate)),
        'discount_group_1': str(money(payload.discount_group_1)),
        'discount_group_2': str(money(payload.discount_group_2)),
        'discount_group_3': str(money(payload.discount_group_3)),
        'discount_group_4': str(money(payload.discount_group_4)),
        'payment_installments': payload.payment_installments,
        'ep_receipts': str(money(payload.ep_receipts)),
        'podatak': {
            '01': payload.room_beds,
            '03': str(payload.room_bed_total),
            '04': payload.aux_beds,
            '06': str(payload.aux_bed_total),
            '25': str(payload.total_assessed),
            '31': str(payload.amount_after_discount),
            '32': payload.lump_sum_flag,
            '33': str(payload.lump_sum_amount),
            '34': payload.installment_flag,
            '35': str(payload.installment_amount),
            '36': str(money(payload.ep_receipts)),
        },
    }


def canonical_json(payload: Tz2Payload) -> str:
    return json.dumps(payload_to_dict(payload), sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def payload_hash(payload: Tz2Payload) -> str:
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()
