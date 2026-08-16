"""Canonical JSON serialization and payload hashing."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal

from accounting.services.tax_forms.pdv.payload import (
    PdvFieldPair,
    PdvMarginPair,
    PdvPayload,
    TaxpayerInfo,
)

_TWO_PLACES = Decimal('0.01')


def _decimal_str(value: Decimal) -> str:
    return str(value.quantize(_TWO_PLACES))


def _serialize_taxpayer(taxpayer: TaxpayerInfo) -> dict[str, str]:
    return {
        'city': taxpayer.city,
        'house_number': taxpayer.house_number,
        'name': taxpayer.name,
        'oib': taxpayer.oib,
        'postal_code': taxpayer.postal_code,
        'street': taxpayer.street,
    }


def _serialize_field_value(value: PdvFieldPair | PdvMarginPair | Decimal | bool) -> object:
    if isinstance(value, PdvFieldPair):
        return {
            'porez': _decimal_str(value.porez),
            'vrijednost': _decimal_str(value.vrijednost),
        }
    if isinstance(value, PdvMarginPair):
        return {
            'nabavna_vrijednost': _decimal_str(value.nabavna_vrijednost),
            'prodajna_vrijednost': _decimal_str(value.prodajna_vrijednost),
        }
    if isinstance(value, Decimal):
        return _decimal_str(value)
    return value


def payload_to_dict(payload: PdvPayload) -> dict:
    fields = {
        code: _serialize_field_value(value)
        for code, value in sorted(payload.fields.items(), key=lambda item: int(item[0]))
    }
    return {
        'fields': fields,
        'mapping_version': payload.mapping_version,
        'period_from': payload.period_from.isoformat(),
        'period_to': payload.period_to.isoformat(),
        'schema_version': payload.schema_version,
        'taxpayer': _serialize_taxpayer(payload.taxpayer),
    }


def canonical_json(payload: PdvPayload) -> str:
    return json.dumps(payload_to_dict(payload), sort_keys=True, separators=(',', ':'))


def payload_hash(payload: PdvPayload) -> str:
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()
