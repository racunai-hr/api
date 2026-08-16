"""Convert UBL documents to Fiskal Platform JSON payloads."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

from fiscal_gateway.adapters.ubl_to_fiscal import ubl_document_to_fiscal_payload
from ubl.domain.document import UblDocument


def _serialize_value(value):
    if is_dataclass(value):
        return {key: _serialize_value(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


def ubl_document_to_platform_payload(document: UblDocument) -> dict:
    outgoing = ubl_document_to_fiscal_payload(document)
    return _serialize_value(outgoing)
