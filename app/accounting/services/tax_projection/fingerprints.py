"""Deterministic projection fingerprints shared by prepare and apply."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from accounting.services.tax_projection.contracts import ProjectedLedgerRow
from accounting.services.tax_projection.identity import identity_of

_TWO_PLACES = Decimal('0.01')


def _money(value: Decimal) -> str:
    return format(Decimal(value or 0).quantize(_TWO_PLACES), 'f')


def writable_rows(rows: tuple[ProjectedLedgerRow, ...]) -> tuple[ProjectedLedgerRow, ...]:
    """Engine-owned projection rows. Manual VIII.1 stay as origin=manual in the ledger."""
    return tuple(row for row in rows if row.source_kind != 'manual_ledger')


def output_fingerprint(rows: tuple[ProjectedLedgerRow, ...]) -> str:
    payload = [
        {
            'identity': list(identity_of(row)),
            'box': row.box,
            'base': _money(row.base_amount),
            'tax': _money(row.tax_amount),
            'sign': format(row.sign, 'f'),
            'rule_code': row.rule_code,
        }
        for row in sorted(rows, key=lambda row: (*identity_of(row), row.box))
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def resolved_source_object_id(row: ProjectedLedgerRow) -> int:
    """Match apply insert: journal lines use line PK as source_object_id like legacy."""
    if row.source_kind == 'journal_line' and row.source_line_id:
        return row.source_line_id
    return row.source_id


def engine_effect_fingerprint_from_rows(rows: tuple[ProjectedLedgerRow, ...]) -> str:
    """Fingerprint of writable candidate rows in the shape stored on VATLedgerEntry."""
    payload = []
    for row in sorted(
        writable_rows(rows),
        key=lambda row: (row.box, resolved_source_object_id(row), row.source_line_id or 0),
    ):
        payload.append(
            [
                row.box,
                _money(row.base_amount * row.sign),
                _money(row.tax_amount * row.sign),
                resolved_source_object_id(row),
                row.source_line_id if row.source_line_id is not None else 0,
            ]
        )
    encoded = json.dumps(payload, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def engine_effect_fingerprint_from_ledger(entries) -> str:
    payload = [
        [
            entry.vat_box,
            _money(entry.base_amount),
            _money(entry.vat_amount),
            entry.source_object_id or 0,
            entry.source_line_id if entry.source_line_id is not None else 0,
        ]
        for entry in sorted(
            entries,
            key=lambda entry: (
                entry.vat_box or '',
                entry.source_object_id or 0,
                entry.source_line_id or 0,
                entry.pk or 0,
            ),
        )
    ]
    encoded = json.dumps(payload, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
