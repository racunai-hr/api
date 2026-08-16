"""RRIF-RP2025 kontni plan — parsiranje i mapiranje."""

from __future__ import annotations

import csv
from pathlib import Path

RRIF_CLASS_TO_ACCOUNT_TYPE = {
    '0': 'asset',
    '1': 'asset',
    '2': 'liability',
    '3': 'asset',
    '4': 'expense',
    '5': 'expense',
    '6': 'asset',
    '7': 'revenue',
    '8': 'equity',
    '9': 'equity',
}

DEFAULT_CSV_PATH = Path(__file__).resolve().parent / 'data' / 'RRiF-RP2025.csv'


def find_parent_code(code: str, existing_codes: set[str]) -> str | None:
    for length in range(len(code) - 1, 0, -1):
        prefix = code[:length]
        if prefix in existing_codes:
            return prefix
    return None


def compute_level(code: str, existing_codes: set[str]) -> int:
    level = 0
    current = code
    while True:
        parent = find_parent_code(current, existing_codes)
        if parent is None:
            return level
        level += 1
        current = parent


def load_rrif_rows(csv_path: Path | None = None) -> list[dict]:
    path = csv_path or DEFAULT_CSV_PATH
    with path.open(encoding='utf-8') as fh:
        reader = csv.DictReader(fh, delimiter=';')
        rows = []
        for row in reader:
            key = (row.get('kljuc') or row.get('ključ') or '').strip()
            name = (row.get('naziv') or '').strip()
            if key and name:
                rows.append({'code': key, 'name': name})
        return rows


def enrich_rrif_rows(rows: list[dict]) -> list[dict]:
    codes = {r['code'] for r in rows}
    child_codes: set[str] = set()
    for code in codes:
        parent = find_parent_code(code, codes)
        if parent:
            child_codes.add(parent)

    enriched = []
    for row in rows:
        code = row['code']
        enriched.append({
            **row,
            'parent_code': find_parent_code(code, codes),
            'account_class': code[0] if code else '',
            'account_type_name': RRIF_CLASS_TO_ACCOUNT_TYPE.get(code[0] if code else '', 'asset'),
            'level': compute_level(code, codes),
            'is_synthetic': code in child_codes,
            'is_postable': code not in child_codes,
        })
    return enriched
