"""SubmissionDocument protocol — tax forms implement this for SubmissionService."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Protocol

from accounting.models import VATPeriod


class SubmissionDocument(Protocol):
    tenant: object

    def get_period(self) -> VATPeriod: ...

    def get_display_name(self) -> str: ...

    def get_version(self) -> int: ...

    def get_payload_hash(self) -> str | None: ...


def _decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal('0.01')))


def _pdv_s_snapshot(*, period_from: date, period_to: date, taxpayer_oib: str, rows) -> dict:
    return {
        'period_from': period_from.isoformat(),
        'period_to': period_to.isoformat(),
        'taxpayer_oib': taxpayer_oib,
        'rows': [
            {
                'country_code': row.country_code,
                'pdv_id': row.pdv_id,
                'goods_value': _decimal_str(row.goods_value),
                'services_value': _decimal_str(row.services_value),
            }
            for row in rows
        ],
    }


def _hash_snapshot(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def pdv_s_payload_hash(pdv_s_return) -> str:
    """Canonical SHA-256 hash of ERP PDV-S aggregate for submission audit."""
    from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows

    payload = aggregate_pdv_s_rows(pdv_s_return.vat_period)
    return pdv_s_payload_hash_from_aggregate(payload)


def pdv_s_payload_hash_from_aggregate(payload) -> str:
    snapshot = _pdv_s_snapshot(
        period_from=payload.period_from,
        period_to=payload.period_to,
        taxpayer_oib=payload.taxpayer.oib,
        rows=payload.rows,
    )
    return _hash_snapshot(snapshot)


def pdv_s_payload_hash_from_xml(xml_bytes: bytes) -> str:
    from accounting.services.tax_forms.pdv_s.parse import parse_pdv_s_xml

    parsed = parse_pdv_s_xml(xml_bytes)

    class _Row:
        def __init__(self, country_code, pdv_id, goods_value, services_value):
            self.country_code = country_code
            self.pdv_id = pdv_id
            self.goods_value = goods_value
            self.services_value = services_value

    rows = [
        _Row(
            row.country_code,
            row.pdv_id,
            row.goods_amount,
            row.services_amount,
        )
        for row in parsed.rows
    ]
    snapshot = _pdv_s_snapshot(
        period_from=date.fromisoformat(parsed.period_from),
        period_to=date.fromisoformat(parsed.period_to),
        taxpayer_oib=parsed.oib,
        rows=rows,
    )
    return _hash_snapshot(snapshot)


def zp_payload_hash(zp_return) -> str:
    """Return stored SHA-256 hash of the ZPReturn payload snapshot."""
    return zp_return.payload_hash


def zp_payload_hash_from_aggregate(payload) -> str:
    from accounting.services.tax_forms.zp.canonical import payload_hash

    return payload_hash(payload)


def zp_payload_hash_from_xml(xml_bytes: bytes) -> str:
    from accounting.services.tax_forms.zp.parse import parse_zp_xml

    parsed = parse_zp_xml(xml_bytes)
    return zp_payload_hash_from_aggregate(parsed)
