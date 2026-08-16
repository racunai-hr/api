from __future__ import annotations

from pathlib import Path

from lxml import etree

SCHEMA_DIR = Path(__file__).resolve().parent.parent / 'schemas'
EFISKALIZACIJA_XSD = SCHEMA_DIR / 'eFiskalizacija' / 'eFiskalizacijaSchema.xsd'


class SchemaValidationError(Exception):
    """Raised when XML fails XSD validation."""


def validate_efiskalizacija_xml(element: etree._Element) -> None:
    if not EFISKALIZACIJA_XSD.is_file():
        raise SchemaValidationError(f'XSD nije pronađen: {EFISKALIZACIJA_XSD}')

    with EFISKALIZACIJA_XSD.open('rb') as schema_file:
        schema_doc = etree.parse(schema_file)
    schema = etree.XMLSchema(schema_doc)

    if not schema.validate(element):
        message = str(schema.error_log.last_error)
        raise SchemaValidationError(message)
