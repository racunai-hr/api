"""XSD validation for unsigned Obrazac PDV v11.0 XML."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

SCHEMA_DIR = Path(__file__).resolve().parents[3] / 'schemas' / 'pdv' / 'v11-0'
PDV_OBRAZAC_XSD = SCHEMA_DIR / 'ObrazacPDV-v11-0.xsd'

_SCHEMA: etree.XMLSchema | None = None


class PdvSchemaValidationError(Exception):
    """Raised when Obrazac PDV XML fails XSD validation."""


def _get_schema() -> etree.XMLSchema:
    global _SCHEMA
    if _SCHEMA is not None:
        return _SCHEMA
    if not PDV_OBRAZAC_XSD.is_file():
        raise PdvSchemaValidationError(f'XSD nije pronađen: {PDV_OBRAZAC_XSD}')
    schema_doc = etree.parse(str(PDV_OBRAZAC_XSD))
    _SCHEMA = etree.XMLSchema(schema_doc)
    return _SCHEMA


_SIGNATURE_NS = 'http://www.w3.org/2000/09/xmldsig#'


def _strip_signatures(document: etree._Element) -> etree._Element:
    for signature in document.findall(f'{{{_SIGNATURE_NS}}}Signature'):
        document.remove(signature)
    return document


def validate_pdv_obrazac_xml(data: bytes | etree._Element, *, signed: bool = False) -> None:
    """Validate Obrazac PDV XML against the local v11.0 XSD.

    Signed uploads are validated on the unsigned body (local signed XSD does not
    cover XAdES SigningCertificateV2) and must contain an XML Signature element.
    """
    document = etree.fromstring(data) if isinstance(data, (bytes, bytearray)) else data
    if signed:
        signatures = document.findall(f'{{{_SIGNATURE_NS}}}Signature')
        if not signatures:
            raise PdvSchemaValidationError('Potpisani XML mora sadržavati XML Signature element.')
        document = _strip_signatures(etree.fromstring(etree.tostring(document)))
    schema = _get_schema()
    if not schema.validate(document):
        message = str(schema.error_log.last_error)
        raise PdvSchemaValidationError(message)
