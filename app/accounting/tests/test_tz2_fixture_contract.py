"""Contract tests for TZ2 fixture files."""

from __future__ import annotations

from django.test import SimpleTestCase

from accounting.tests.fixtures.tax.tz2.load import SCENARIO_IDS, TZ2_FIXTURES_ROOT, load_scenario


class Tz2FixtureContractTests(SimpleTestCase):
    def test_all_scenarios_exist_on_disk(self):
        for scenario_id in SCENARIO_IDS:
            self.assertTrue((TZ2_FIXTURES_ROOT / scenario_id).is_dir(), scenario_id)

    def test_alma_2026_structure(self):
        data = load_scenario('alma_2026')
        expected = data['expected']
        self.assertEqual(expected['podatak01'], 10)
        self.assertEqual(expected['podatak04'], 0)
        self.assertEqual(expected['podatak34'], '1')
        self.assertEqual(expected['podatak36'], '17360.00')
        self.assertEqual(expected['taxpayer']['house_number'], '0009')
        self.assertEqual(expected['taxpayer']['municipality_code'], '500')

    def test_official_example_totals(self):
        data = load_scenario('official_example')
        self.assertEqual(data['expected']['podatak25'], '392.50')
        self.assertEqual(data['expected']['podatak32'], '1')

    def test_missing_last_name_expects_validation_error(self):
        data = load_scenario('missing_last_name')
        self.assertNotIn('expected', data)
        self.assertTrue(data['validation']['expect_error'])

    def test_carolina_2026_xml_identical_pdfs_differ(self):
        import hashlib

        root = TZ2_FIXTURES_ROOT / 'carolina_2026'
        submitted_xml = (root / 'submitted.xml').read_bytes()
        processed_xml = (root / 'processed.xml').read_bytes()
        submitted_pdf = (root / 'submitted.pdf').read_bytes()
        processed_pdf = (root / 'processed.pdf').read_bytes()
        self.assertEqual(submitted_xml, processed_xml)
        self.assertEqual(
            hashlib.sha256(submitted_xml).hexdigest(),
            '36720c22995f5ebbb6e322ac45c801c902a9d2a471ef3e3751e92bd558201664',
        )
        self.assertNotEqual(submitted_pdf, processed_pdf)
        self.assertEqual(
            hashlib.sha256(submitted_pdf).hexdigest(),
            '0c6f51dd0b79099060737d372d3875bce0e6eb177a0f3125e3efd20aeba3cca5',
        )
        self.assertEqual(
            hashlib.sha256(processed_pdf).hexdigest(),
            'a252d8a0ffd9df68656646a1eae4f29693d92b65978f19dc7f3c9bcdbe2fa362',
        )
