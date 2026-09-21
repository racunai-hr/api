"""TZ2 arithmetic verification."""

from __future__ import annotations

from django.test import SimpleTestCase

from accounting.services.tax_forms.tz2.build import build_tz2_payload
from accounting.services.tax_forms.tz2.verify import verify_tz2_arithmetic
from accounting.tests.fixtures.tax.tz2.load import load_scenario
from accounting.tests.test_tz2_build import _input_from_fixture


class Tz2VerifyTests(SimpleTestCase):
    def test_alma_arithmetic_clean(self):
        data = load_scenario('alma_2026')
        year, build_input = _input_from_fixture(data['input'])
        payload = build_tz2_payload(year, build_input)
        self.assertEqual(verify_tz2_arithmetic(payload), [])
