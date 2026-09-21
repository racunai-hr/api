"""Round-trip payload → XML → payload for TZ 2."""

from __future__ import annotations

from django.test import SimpleTestCase

from accounting.services.tax_forms.tz2.build import build_tz2_payload
from accounting.services.tax_forms.tz2.parse import parse_tz2_xml
from accounting.services.tax_forms.tz2.render import render_tz2_xml
from accounting.services.tax_forms.tz2.validation import validate_tz2_xml
from accounting.tests.fixtures.tax.tz2.load import load_scenario
from accounting.tests.test_tz2_build import _input_from_fixture


class Tz2RenderParseRoundtripTests(SimpleTestCase):
    def test_alma_roundtrip(self):
        data = load_scenario('alma_2026')
        year, build_input = _input_from_fixture(data['input'])
        original = build_tz2_payload(year, build_input)
        xml_bytes = render_tz2_xml(original)
        validate_tz2_xml(xml_bytes)
        parsed = parse_tz2_xml(xml_bytes)
        self.assertEqual(parsed.taxpayer.oib, original.taxpayer.oib)
        self.assertEqual(parsed.taxpayer.house_number, '0009')
        self.assertEqual(parsed.room_beds, 10)
        self.assertEqual(parsed.aux_beds, 0)
        self.assertTrue(parsed.payment_installments)
        self.assertEqual(parsed.ep_receipts, original.ep_receipts)
        self.assertEqual(parsed.room_bed_total, original.room_bed_total)
        self.assertEqual(parsed.installment_amount, original.installment_amount)

    def test_official_example_roundtrip(self):
        data = load_scenario('official_example')
        year, build_input = _input_from_fixture(data['input'])
        original = build_tz2_payload(year, build_input)
        parsed = parse_tz2_xml(render_tz2_xml(original))
        self.assertEqual(parsed.total_assessed, original.total_assessed)
        self.assertFalse(parsed.payment_installments)
