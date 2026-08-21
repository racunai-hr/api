"""Provenance wrapper for status and derived fields (ADR-0020 §4)."""

from __future__ import annotations

from typing import Any

REASONS = frozenset({'not_recorded', 'not_provable', 'not_applicable'})


def provenanced(value: Any, *, source: str | None = None, reason: str | None = None) -> dict:
    if value is None:
        if reason not in REASONS:
            raise ValueError(f'null provenance requires reason in {sorted(REASONS)}')
        return {'value': None, 'reason': reason, 'source': None}
    if reason is not None:
        raise ValueError('non-null provenance cannot set reason')
    if not source:
        raise ValueError('non-null provenance requires source')
    return {'value': value, 'reason': None, 'source': source}


def is_known(field: dict) -> bool:
    return field.get('value') is not None
