from __future__ import annotations

import csv
from io import StringIO

CAMT053_NS = 'urn:iso:std:iso:20022:tech:xsd:camt.053'

_CSV_HEADER_MARKERS = frozenset({
    'datum', 'iznos', 'opis', 'poziv', 'iban',
    'date', 'amount', 'description', 'reference',
    'booking date', 'value date',
})


def detect_format(content: bytes | str, filename: str = '') -> str:
    text = _as_text(content)
    stripped = text.lstrip()

    if CAMT053_NS in stripped:
        return 'camt053'

    if ':20:' in text and ':25:' in text:
        return 'mt940'

    if _looks_like_csv(text):
        return 'csv'

    lower_name = filename.lower()
    if lower_name.endswith('.xml'):
        return 'camt053'
    if lower_name.endswith('.csv'):
        return 'csv'
    if lower_name.endswith('.sta'):
        return 'mt940'

    return 'unknown'


def _as_text(content: bytes | str) -> str:
    if isinstance(content, bytes):
        return content.decode('utf-8', errors='replace')
    return content


def _looks_like_csv(text: str) -> bool:
    sample = text[:4096]
    if not sample.strip():
        return False

    delimiter = ';' if sample.count(';') >= sample.count(',') else ','
    try:
        reader = csv.reader(StringIO(sample), delimiter=delimiter)
        header = next(reader, None)
    except csv.Error:
        return False

    if not header:
        return False

    normalized = {cell.strip().lower() for cell in header if cell.strip()}
    return len(normalized & _CSV_HEADER_MARKERS) >= 2
