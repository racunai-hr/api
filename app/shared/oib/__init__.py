"""OIB normalization and checksum validation (ADR-0023)."""

from __future__ import annotations

import re

_OIB_RE = re.compile(r'\D')
_PLACEHOLDERS = frozenset({'00000000000', '11111111111', '99999999999'})


def normalize_oib(value: str) -> str:
    """Strip HR prefix and non-digits from an OIB string."""
    cleaned = (value or '').replace('HR', '').replace('hr', '').strip()
    return _OIB_RE.sub('', cleaned)


def oib_checksum_valid(oib: str) -> bool:
    """ISO 7064 MOD 11,10 control digit for an 11-digit OIB."""
    if len(oib) != 11 or not oib.isdigit():
        return False
    digits = [int(c) for c in oib]
    check = 10
    for digit in digits[:10]:
        check = (check + digit) % 10
        if check == 0:
            check = 10
        check = (check * 2) % 11
    control = 11 - check
    if control == 10:
        control = 0
    return control == digits[10]


def is_valid_oib(value: str) -> bool:
    """True when value normalizes to a non-placeholder OIB with valid checksum."""
    oib = normalize_oib(value)
    if len(oib) != 11 or not oib.isdigit():
        return False
    if oib in _PLACEHOLDERS:
        return False
    return oib_checksum_valid(oib)


__all__ = ['is_valid_oib', 'normalize_oib', 'oib_checksum_valid']
