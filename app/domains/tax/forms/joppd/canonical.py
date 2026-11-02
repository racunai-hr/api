"""Canonical JSON and payload hash for JOPPD."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from domains.tax.forms.joppd.payload import JoppdPayload

_MONEY = Decimal('0.01')


def _money(value: Decimal) -> str:
    return str(value.quantize(_MONEY))


def payload_to_dict(payload: JoppdPayload) -> dict:
    return {
        'schema_version': payload.schema_version,
        'mapping_version': payload.mapping_version,
        'period_year': payload.period_year,
        'period_month': payload.period_month,
        'payment_date': payload.payment_date.isoformat(),
        'employer': {
            'name': payload.employer.name,
            'oib': payload.employer.oib,
            'street': payload.employer.street,
            'house_number': payload.employer.house_number,
            'city': payload.employer.city,
            'postal_code': payload.employer.postal_code,
        },
        'prepared_by': {
            'first_name': payload.prepared_by.first_name,
            'last_name': payload.prepared_by.last_name,
        },
        'rows': [
            {
                'recipient_oib': row.recipient_oib,
                'first_name': row.first_name,
                'last_name': row.last_name,
                'date_of_birth': row.date_of_birth.isoformat() if row.date_of_birth else None,
                'municipality_code': row.municipality_code,
                'recipient_code': row.recipient_code,
                'income_type_code': row.income_type_code,
                'date_from': row.date_from.isoformat(),
                'date_to': row.date_to.isoformat(),
                'is_first_month': row.is_first_month,
                'is_last_month': row.is_last_month,
                'hours_total': str(row.hours_total),
                'gross_salary': _money(row.gross_salary),
                'mio1_base': _money(row.mio1_base),
                'mio1_amount': _money(row.mio1_amount),
                'mio2_amount': _money(row.mio2_amount),
                'income_base': _money(row.income_base),
                'personal_allowance_amount': _money(row.personal_allowance_amount),
                'tax_base': _money(row.tax_base),
                'income_tax_rate_used': str(row.income_tax_rate_used),
                'income_tax_amount': _money(row.income_tax_amount),
                'net_salary': _money(row.net_salary),
                'zo_amount': _money(row.zo_amount),
                'total_labor_cost': _money(row.total_labor_cost),
            }
            for row in payload.rows
        ],
    }


def canonical_json(payload: JoppdPayload) -> str:
    return json.dumps(payload_to_dict(payload), sort_keys=True, separators=(',', ':'))


def payload_hash(payload: JoppdPayload) -> str:
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()
