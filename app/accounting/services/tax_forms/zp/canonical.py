"""Canonical JSON and payload hash for ZP."""

from __future__ import annotations

import hashlib
import json

from decimal import Decimal

from accounting.services.tax_forms.zp.payload import ZpPayload

_MONEY = Decimal('0.01')


def payload_to_dict(payload: ZpPayload) -> dict:
    return {
        'schema_version': payload.schema_version,
        'mapping_version': payload.mapping_version,
        'period_from': payload.period_from.isoformat(),
        'period_to': payload.period_to.isoformat(),
        'taxpayer': {
            'name': payload.taxpayer.name,
            'oib': payload.taxpayer.oib,
            'city': payload.taxpayer.city,
            'street': payload.taxpayer.street,
            'house_number': payload.taxpayer.house_number,
        },
        'prepared_by': {
            'first_name': payload.prepared_by.first_name,
            'last_name': payload.prepared_by.last_name,
        },
        'tax_office_code': payload.tax_office_code,
        'rows': [
            {
                'country_code': row.country_code,
                'pdv_id': row.pdv_id,
                'goods_value': str(row.goods_value.quantize(_MONEY)),
                'services_value': str(row.services_value.quantize(_MONEY)),
            }
            for row in payload.rows
        ],
    }


def canonical_json(payload: ZpPayload) -> str:
    return json.dumps(payload_to_dict(payload), sort_keys=True, separators=(',', ':'))


def payload_hash(payload: ZpPayload) -> str:
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()
