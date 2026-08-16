"""Regression tests for submitted PDV XML archive (Fine Star 01–04/2026)."""

import json
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from accounting.services.tax_forms.pdv.canonical import canonical_json, payload_hash
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.payload import PdvFieldPair
from accounting.services.tax_forms.pdv.validation import validate_pdv_obrazac_xml

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_ARCHIVE = _FIXTURES / 'archive'
_IMPLEMENTED_PAIR_BOXES = ('201', '202', '203', '303')
_FINE_STAR_OIB = '36619131370'


class PdvSubmittedArchiveRegressionTests(SimpleTestCase):
    """Each archived submitted XML must parse, validate, and produce stable canonical JSON."""

    def test_archive_contains_xml_files(self):
        xml_files = list(_ARCHIVE.glob('PDV_*.xml'))
        self.assertGreaterEqual(len(xml_files), 5, 'Expected Fine Star archive 01–05/2026')

    def test_each_archive_xml_parses_and_validates(self):
        for xml_path in sorted(_ARCHIVE.glob('PDV_*.xml')):
            with self.subTest(file=xml_path.name):
                xml_bytes = xml_path.read_bytes()
                is_signed = b'SignatureValue' in xml_bytes
                validate_pdv_obrazac_xml(xml_bytes, signed=is_signed)
                payload = parse_pdv_obrazac_xml(xml_bytes)
                self.assertEqual(payload.taxpayer.oib, _FINE_STAR_OIB)
                self.assertEqual(len(payload_hash(payload)), 64)
                first = json.loads(canonical_json(payload))
                reparsed = parse_pdv_obrazac_xml(xml_bytes)
                second = json.loads(canonical_json(reparsed))
                self.assertEqual(first, second)

    def test_april_2026_archive_matches_expected_fixture(self):
        april_xml = _ARCHIVE / 'PDV_36619131370_20260401-20260430.xml'
        if not april_xml.is_file():
            april_xml = _FIXTURES / 'submitted_april_2026.xml'
        payload = parse_pdv_obrazac_xml(april_xml.read_bytes())
        expected = json.loads((_FIXTURES / 'expected_payload_april_2026.json').read_text(encoding='utf-8'))
        self.assertEqual(json.loads(canonical_json(payload)), expected)

    def test_implemented_boxes_are_decimal_pairs_in_archive(self):
        for xml_path in sorted(_ARCHIVE.glob('PDV_*.xml')):
            with self.subTest(file=xml_path.name):
                payload = parse_pdv_obrazac_xml(xml_path.read_bytes())
                for code in _IMPLEMENTED_PAIR_BOXES:
                    value = payload.fields.get(code)
                    self.assertIsInstance(
                        value,
                        PdvFieldPair,
                        f'{xml_path.name}: box {code} should be PdvFieldPair',
                    )
                    self.assertIsInstance(value.vrijednost, Decimal)
                    self.assertIsInstance(value.porez, Decimal)

    def test_field_400_is_computed_scalar_in_archive(self):
        for xml_path in sorted(_ARCHIVE.glob('PDV_*.xml')):
            with self.subTest(file=xml_path.name):
                payload = parse_pdv_obrazac_xml(xml_path.read_bytes())
                field_400 = payload.fields.get('400')
                self.assertIsInstance(field_400, Decimal, f'{xml_path.name}: Podatak400')
