"""XSD validation for generated Obrazac ZP XML."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from accounting.services.tax_forms.zp.payload import ZpPayload, ZpPreparedBy, ZpRow, ZpTaxpayer
from accounting.services.tax_forms.zp.render import render_zp_xml
from accounting.services.tax_forms.zp.validation import validate_zp_xml
from accounting.tests.fixtures.tax.zp.load import load_scenario


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


class ZpXsdValidationTests(SimpleTestCase):
    def _assert_valid_xml(self, expected: dict) -> None:
        xml_bytes = render_zp_xml(_payload_from_expected(expected))
        validate_zp_xml(xml_bytes)

    def test_single_eu_partner(self):
        self._assert_valid_xml(load_scenario('single_eu_partner')['expected'])

    def test_multiple_eu_partners(self):
        self._assert_valid_xml(load_scenario('multiple_eu_partners')['expected'])

    def test_empty_period(self):
        self._assert_valid_xml(load_scenario('empty_period')['expected'])

    def test_period_correction_v2(self):
        self._assert_valid_xml(load_scenario('period_correction')['expected_v2'])
