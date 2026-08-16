"""Round-trip tests: ZpPayload → render → parse."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from accounting.services.tax_forms.zp.parse import parse_zp_xml
from accounting.services.tax_forms.zp.payload import ZpPayload, ZpPreparedBy, ZpRow, ZpTaxpayer
from accounting.services.tax_forms.zp.render import render_zp_xml
from accounting.services.tax_forms.zp.verify import compare_zp_payload_fields
from accounting.tests.fixtures.tax.zp.load import SCENARIO_IDS, load_scenario


def _payload_from_expected(expected: dict) -> ZpPayload:
    return ZpPayload(
        period_from=date.fromisoformat(expected['period_from']),
        period_to=date.fromisoformat(expected['period_to']),
        taxpayer=ZpTaxpayer(**expected['taxpayer']),
        prepared_by=ZpPreparedBy(**expected['prepared_by']),
        tax_office_code=expected['tax_office_code'],
        rows=tuple(
            ZpRow(
                country_code=row['country_code'],
                pdv_id=row['pdv_id'],
                goods_value=Decimal(row['goods_value']),
                services_value=Decimal(row['services_value']),
            )
            for row in expected['rows']
        ),
        schema_version=expected['schema_version'],
        mapping_version=expected['mapping_version'],
    )


class ZpRenderParseRoundtripTests(SimpleTestCase):
    def _assert_roundtrip(self, expected: dict) -> None:
        original = _payload_from_expected(expected)
        parsed = parse_zp_xml(render_zp_xml(original))
        self.assertEqual([], compare_zp_payload_fields(original, parsed))

    def test_single_eu_partner(self):
        self._assert_roundtrip(load_scenario('single_eu_partner')['expected'])

    def test_multiple_eu_partners(self):
        self._assert_roundtrip(load_scenario('multiple_eu_partners')['expected'])

    def test_empty_period(self):
        self._assert_roundtrip(load_scenario('empty_period')['expected'])

    def test_pdv_mismatch_scenario(self):
        self._assert_roundtrip(load_scenario('pdv_mismatch_101_103')['expected'])

    def test_period_correction_v1(self):
        self._assert_roundtrip(load_scenario('period_correction')['expected_v1'])

    def test_period_correction_v2(self):
        self._assert_roundtrip(load_scenario('period_correction')['expected_v2'])

    def test_all_payload_scenarios_have_expected(self):
        for scenario_id in SCENARIO_IDS:
            if scenario_id in {'invalid_vat_id'}:
                continue
            data = load_scenario(scenario_id)
            if scenario_id == 'period_correction':
                self.assertIn('expected_v1', data)
                self.assertIn('expected_v2', data)
            else:
                self.assertIn('expected', data)
