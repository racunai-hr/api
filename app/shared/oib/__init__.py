"""OIB normalization — no checksum validation yet."""

import re

_OIB_RE = re.compile(r'\D')


def normalize_oib(value: str) -> str:
    """Strip HR prefix and non-digits from an OIB string."""
    cleaned = (value or '').replace('HR', '').strip()
    return _OIB_RE.sub('', cleaned)


__all__ = ['normalize_oib']
