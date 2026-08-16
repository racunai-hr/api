from __future__ import annotations

from collections import Counter
from pathlib import Path

from expenses.parsers.f1_csv import F1CsvParser

_PARSER = F1CsvParser()


def extract_supplier_oibs(content: bytes | str, *, filename: str = '') -> set[str]:
    """Extract unique supplier OIBs from a single F1 CSV file."""
    result = _PARSER.parse(content, filename=filename)
    oibs: set[str] = set()
    for row in result.rows:
        oib = (row.supplier_oib or '').strip()
        if oib:
            oibs.add(oib)
    return oibs


def extract_supplier_oibs_from_paths(paths: list[str | Path]) -> dict[str, int]:
    """Aggregate supplier OIB receipt counts from one or more F1 CSV files."""
    counts: Counter[str] = Counter()
    for path in paths:
        file_path = Path(path)
        content = file_path.read_bytes()
        result = _PARSER.parse(content, filename=file_path.name)
        for row in result.rows:
            oib = (row.supplier_oib or '').strip()
            if oib:
                counts[oib] += 1
    return dict(counts)


def resolve_csv_paths(path: str | Path) -> list[Path]:
    """Resolve a file or directory path to a list of F1 CSV files."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f'Putanja ne postoji: {target}')

    if target.is_file():
        return [target]

    csv_files = sorted(target.glob('*.csv'))
    if not csv_files:
        raise FileNotFoundError(f'Nema CSV datoteka u: {target}')
    return csv_files
