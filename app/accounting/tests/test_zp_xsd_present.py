"""Smoke test: local ZP XSD loads and validates the official example XML."""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase
from lxml import etree

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / 'schemas' / 'zp' / 'v1-0'
ZP_OBRAZAC_XSD = _SCHEMA_DIR / 'ObrazacZP-v1-0.xsd'
ZP_EXAMPLE_XML = _SCHEMA_DIR / 'examples' / 'Primjer.xml'


class ZpXsdPresentTests(SimpleTestCase):
    def test_schema_files_are_available_locally(self):
        self.assertTrue(ZP_OBRAZAC_XSD.is_file(), msg=str(ZP_OBRAZAC_XSD))
        etree.XMLSchema(etree.parse(str(ZP_OBRAZAC_XSD)))

    def test_official_example_passes_xsd(self):
        self.assertTrue(ZP_EXAMPLE_XML.is_file(), msg=str(ZP_EXAMPLE_XML))
        schema = etree.XMLSchema(etree.parse(str(ZP_OBRAZAC_XSD)))
        document = etree.parse(str(ZP_EXAMPLE_XML))
        if not schema.validate(document):
            self.fail(f'Official ZP example failed XSD validation: {schema.error_log.last_error}')
