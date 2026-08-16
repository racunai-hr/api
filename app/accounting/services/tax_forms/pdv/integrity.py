"""VATReturn draft integrity — semantic payload↔XML sync and byte tamper detection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from django.core.files.storage import default_storage

from accounting.models import VATReturn
from accounting.services.tax_forms.pdv.diff import PdvFieldDifference, compare_pdv_payload_fields
from accounting.services.tax_forms.pdv.import_return import load_payload_from_file
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml

_MAX_DIFF_SUMMARY = 5


@dataclass(frozen=True)
class VatReturnIntegrity:
    payload_file_ok: bool
    xml_file_ok: bool
    fields_match: bool
    xml_bytes_match: bool
    status: Literal['SYNC', 'OUT_OF_SYNC']
    differences: list[PdvFieldDifference]


class VatReturnOutOfSyncError(Exception):
    """Raised when a VATReturn draft is not safe to download or submit."""

    def __init__(self, integrity: VatReturnIntegrity) -> None:
        self.integrity = integrity
        super().__init__(self.summary())

    def summary(self) -> str:
        parts: list[str] = []
        if not self.integrity.payload_file_ok:
            parts.append('payload.json ne odgovara hashu')
        if not self.integrity.xml_file_ok:
            parts.append('unsigned.xml nedostaje')
        if not self.integrity.fields_match:
            count = len(self.integrity.differences)
            if count == 1:
                parts.append('1 razlika u poljima payload↔XML')
            else:
                parts.append(f'{count} razlika u poljima payload↔XML')
        if not self.integrity.xml_bytes_match:
            parts.append('SHA256 unsigned.xml ne odgovara arhiviranom hashu')
        if not parts:
            return 'PDV obrazac nije usklađen'
        return '; '.join(parts)


def check_vat_return_integrity(vat_return: VATReturn) -> VatReturnIntegrity:
    """Check payload file, XML file, semantic field match (I001) and byte hash (I002)."""
    payload_file_ok = False
    xml_file_ok = False
    fields_match = False
    xml_bytes_match = False
    differences: list[PdvFieldDifference] = []
    xml_bytes: bytes | None = None

    if vat_return.payload_json and default_storage.exists(vat_return.payload_json.name):
        payload_bytes = default_storage.open(vat_return.payload_json.name).read()
        payload_file_ok = (
            hashlib.sha256(payload_bytes).hexdigest() == vat_return.payload_hash
        )

    if vat_return.xml_unsigned and default_storage.exists(vat_return.xml_unsigned.name):
        xml_file_ok = True
        xml_bytes = default_storage.open(vat_return.xml_unsigned.name).read()

    if payload_file_ok and xml_file_ok and xml_bytes is not None:
        try:
            expected = load_payload_from_file(vat_return)
            actual = parse_pdv_obrazac_xml(xml_bytes)
            differences = compare_pdv_payload_fields(expected, actual)
            fields_match = not differences
        except Exception:
            fields_match = False

    if xml_bytes is not None:
        if vat_return.unsigned_xml_sha256:
            xml_bytes_match = (
                hashlib.sha256(xml_bytes).hexdigest() == vat_return.unsigned_xml_sha256
            )
        else:
            xml_bytes_match = True

    status: Literal['SYNC', 'OUT_OF_SYNC'] = (
        'SYNC'
        if payload_file_ok and xml_file_ok and fields_match and xml_bytes_match
        else 'OUT_OF_SYNC'
    )

    return VatReturnIntegrity(
        payload_file_ok=payload_file_ok,
        xml_file_ok=xml_file_ok,
        fields_match=fields_match,
        xml_bytes_match=xml_bytes_match,
        status=status,
        differences=differences,
    )


def assert_vat_return_sync(vat_return: VATReturn) -> VatReturnIntegrity:
    """Return integrity when SYNC; raise VatReturnOutOfSyncError otherwise."""
    integrity = check_vat_return_integrity(vat_return)
    if integrity.status != 'SYNC':
        raise VatReturnOutOfSyncError(integrity)
    return integrity


def format_integrity_differences(
    differences: list[PdvFieldDifference],
    *,
    limit: int = _MAX_DIFF_SUMMARY,
) -> list[str]:
    """Human-readable diff lines for admin display."""
    lines: list[str] = []
    for diff in differences[:limit]:
        lines.append(f'{diff.field}: {diff.expected} ≠ {diff.actual}')
    remaining = len(differences) - limit
    if remaining > 0:
        lines.append(f'… i još {remaining} razlika')
    return lines
