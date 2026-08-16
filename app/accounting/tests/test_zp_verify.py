"""Tests for ZP verify module (partial — no full ZP pipeline yet)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from accounting.services.tax_forms.zp.payload import ZpPayload, ZpPreparedBy, ZpRow, ZpTaxpayer
from accounting.services.tax_forms.zp.verify import verify_zp_against_pdv_boxes
from accounting.tests.fixtures.tax.zp.load import load_scenario


class ZpVerifyTests(SimpleTestCase):
    def test_single_eu_partner_aligned_with_pdv_cross_check(self):
        expected = load_scenario('single_eu_partner')['expected']
        row = expected['rows'][0]
        payload = ZpPayload(
            period_from=date.fromisoformat(expected['period_from']),
            period_to=date.fromisoformat(expected['period_to']),
            taxpayer=ZpTaxpayer(**expected['taxpayer']),
            prepared_by=ZpPreparedBy(**expected['prepared_by']),
            tax_office_code=expected['tax_office_code'],
            rows=(
                ZpRow(
                    country_code=row['country_code'],
                    pdv_id=row['pdv_id'],
                    goods_value=Decimal(row['goods_value']),
                    services_value=Decimal(row['services_value']),
                ),
            ),
        )
        pdv = load_scenario('single_eu_partner')['pdv']
        result = verify_zp_against_pdv_boxes(
            payload,
            pdv_box_101=Decimal(pdv['fields']['101']),
            pdv_box_103=Decimal(pdv['fields']['103']),
        )
        self.assertTrue(result.is_aligned)

    def test_pdv_mismatch_fixture_fails_cross_check(self):
        expected = load_scenario('pdv_mismatch_101_103')['expected']
        row = expected['rows'][0]
        payload = ZpPayload(
            period_from=date.fromisoformat(expected['period_from']),
            period_to=date.fromisoformat(expected['period_to']),
            taxpayer=ZpTaxpayer(**expected['taxpayer']),
            prepared_by=ZpPreparedBy(**expected['prepared_by']),
            tax_office_code=expected['tax_office_code'],
            rows=(
                ZpRow(
                    country_code=row['country_code'],
                    pdv_id=row['pdv_id'],
                    goods_value=Decimal(row['goods_value']),
                    services_value=Decimal(row['services_value']),
                ),
            ),
        )
        pdv = load_scenario('pdv_mismatch_101_103')['pdv']
        result = verify_zp_against_pdv_boxes(
            payload,
            pdv_box_101=Decimal(pdv['fields']['101']),
            pdv_box_103=Decimal(pdv['fields']['103']),
        )
        self.assertFalse(result.is_aligned)
