from __future__ import annotations

from pathlib import Path

from lxml import etree

from ubl.domain.errors import XsdValidationError

_XSD_DIR = Path(__file__).resolve().parent.parent / 'xsd'
_INVOICE_XSD = _XSD_DIR / 'maindoc' / 'UBL-Invoice-2.1.xsd'

_SCHEMA: etree.XMLSchema | None = None


def _get_schema() -> etree.XMLSchema:
    global _SCHEMA
    if _SCHEMA is not None:
        return _SCHEMA
    if not _INVOICE_XSD.exists():
        raise XsdValidationError(['UBL Invoice XSD nije dostupan u repou'])
    schema_doc = etree.parse(str(_INVOICE_XSD))
    _SCHEMA = etree.XMLSchema(schema_doc)
    return _SCHEMA


def validate_xsd(xml: str) -> None:
    schema = _get_schema()
    doc = etree.fromstring(xml.encode('utf-8'))
    if not schema.validate(doc):
        errors = [str(err) for err in schema.error_log]
        raise XsdValidationError(errors[:10])
