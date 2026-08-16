from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree
from lxml.isoschematron import Schematron

from ubl.domain.errors import SchematronValidationError

_SCHEMATRON_DIR = Path(__file__).resolve().parent.parent / 'schematron'
_SCHEMATRON_CACHE: dict[str, Schematron] = {}
_EXTRACTED_FROM_ZIP = False


class SchematronSkipped(Exception):
    """Raised internally when no schematron patterns are available."""

    pass


def schematron_available() -> bool:
    return bool(_schematron_patterns())


def _extract_pu_zip() -> None:
    """Extract HRUBLSchematron*.zip into schematron dir (once per process)."""
    global _EXTRACTED_FROM_ZIP
    if _EXTRACTED_FROM_ZIP:
        return
    _EXTRACTED_FROM_ZIP = True
    for zip_path in sorted(_SCHEMATRON_DIR.glob('HRUBLSchematron*.zip')):
        try:
            with zipfile.ZipFile(zip_path) as archive:
                for name in archive.namelist():
                    if name.endswith(('.sch', '.xsl')):
                        target = _SCHEMATRON_DIR / Path(name).name
                        if not target.exists():
                            target.write_bytes(archive.read(name))
        except zipfile.BadZipFile:
            continue


def _schematron_patterns() -> list[Path]:
    _extract_pu_zip()
    patterns: list[Path] = []
    for pattern in ('*.xsl', '*.sch'):
        patterns.extend(sorted(_SCHEMATRON_DIR.glob(pattern)))
    return patterns


def _get_schematron(path: Path) -> Schematron:
    key = str(path)
    cached = _SCHEMATRON_CACHE.get(key)
    if cached is not None:
        return cached
    compiled = Schematron(etree.parse(str(path)))
    _SCHEMATRON_CACHE[key] = compiled
    return compiled


def validate_schematron(xml: str) -> None:
    """HR CIUS 2025 Schematron — no-op when patterns are absent (caller logs skipped)."""
    patterns = _schematron_patterns()
    if not patterns:
        raise SchematronSkipped('HR CIUS Schematron sheme nisu u repou')

    doc = etree.fromstring(xml.encode('utf-8'))
    for pattern in patterns:
        schematron = _get_schematron(pattern)
        if not schematron.validate(doc):
            errors = [str(err) for err in schematron.error_log]
            raise SchematronValidationError(errors)
