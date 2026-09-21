"""XSD validation for generated Obrazac TZ 2 XML."""

from __future__ import annotations

from django.test import SimpleTestCase

from accounting.services.tax_forms.tz2.build import build_tz2_payload
from accounting.services.tax_forms.tz2.render import render_tz2_xml
from accounting.services.tax_forms.tz2.validation import validate_tz2_xml
from accounting.tests.fixtures.tax.tz2.load import load_scenario
from accounting.tests.test_tz2_build import _input_from_fixture


class Tz2XsdValidationTests(SimpleTestCase):
    def test_generated_scenarios_pass_xsd(self):
        for scenario_id in ('official_example', 'empty', 'alma_2026'):
            data = load_scenario(scenario_id)
            year, build_input = _input_from_fixture(data['input'])
            xml_bytes = render_tz2_xml(build_tz2_payload(year, build_input))
            validate_tz2_xml(xml_bytes)
