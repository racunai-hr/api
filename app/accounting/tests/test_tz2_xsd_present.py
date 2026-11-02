"""Smoke test: local TZ2 XSD loads and validates the official example XML."""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase
from lxml import etree

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / 'schemas' / 'tz2' / 'v1-0'
TZ2_OBRAZAC_XSD = _SCHEMA_DIR / 'ObrazacTZ2-v1-0.xsd'
TZ2_EXAMPLE_XML = _SCHEMA_DIR / 'examples' / 'Primjer.xml'


class Tz2XsdPresentTests(SimpleTestCase):
    def test_schema_files_are_available_locally(self):
        self.assertTrue(TZ2_OBRAZAC_XSD.is_file(), msg=str(TZ2_OBRAZAC_XSD))
        etree.XMLSchema(etree.parse(str(TZ2_OBRAZAC_XSD)))

    def test_official_example_passes_xsd(self):
        self.assertTrue(TZ2_EXAMPLE_XML.is_file(), msg=str(TZ2_EXAMPLE_XML))
        schema = etree.XMLSchema(etree.parse(str(TZ2_OBRAZAC_XSD)))
        document = etree.parse(str(TZ2_EXAMPLE_XML))
        if not schema.validate(document):
            self.fail(f'Official TZ2 example failed XSD validation: {schema.error_log.last_error}')
