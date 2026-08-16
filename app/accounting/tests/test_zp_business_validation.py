"""Business validation for ZpPayload."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from accounting.services.tax_forms.zp.payload import ZpPayload, ZpPreparedBy, ZpRow, ZpTaxpayer
from accounting.services.tax_forms.zp.validation import ZpValidationError, validate_zp_payload
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


class ZpBusinessValidationTests(SimpleTestCase):
    def test_fixture_payloads_pass(self):
        for scenario_id in ('single_eu_partner', 'multiple_eu_partners', 'empty_period'):
            expected = load_scenario(scenario_id)['expected']
            validate_zp_payload(_payload_from_expected(expected))

    def test_invalid_oib_rejected(self):
        payload = _payload_from_expected(load_scenario('single_eu_partner')['expected'])
        invalid = ZpPayload(
            period_from=payload.period_from,
            period_to=payload.period_to,
            taxpayer=ZpTaxpayer(
                name=payload.taxpayer.name,
                oib='123',
                city=payload.taxpayer.city,
                street=payload.taxpayer.street,
                house_number=payload.taxpayer.house_number,
            ),
            prepared_by=payload.prepared_by,
            tax_office_code=payload.tax_office_code,
            rows=payload.rows,
        )
        with self.assertRaises(ZpValidationError):
            validate_zp_payload(invalid)

    def test_invalid_country_rejected(self):
        payload = _payload_from_expected(load_scenario('single_eu_partner')['expected'])
        row = payload.rows[0]
        invalid = ZpPayload(
            period_from=payload.period_from,
            period_to=payload.period_to,
            taxpayer=payload.taxpayer,
            prepared_by=payload.prepared_by,
            tax_office_code=payload.tax_office_code,
            rows=(
                ZpRow(
                    country_code='XX',
                    pdv_id=row.pdv_id,
                    goods_value=row.goods_value,
                    services_value=row.services_value,
                ),
            ),
        )
        with self.assertRaises(ZpValidationError):
            validate_zp_payload(invalid)

    def test_invalid_pdv_id_rejected(self):
        payload = _payload_from_expected(load_scenario('single_eu_partner')['expected'])
        row = payload.rows[0]
        invalid = ZpPayload(
            period_from=payload.period_from,
            period_to=payload.period_to,
            taxpayer=payload.taxpayer,
            prepared_by=payload.prepared_by,
            tax_office_code=payload.tax_office_code,
            rows=(
                ZpRow(
                    country_code=row.country_code,
                    pdv_id='',
                    goods_value=row.goods_value,
                    services_value=row.services_value,
                ),
            ),
        )
        with self.assertRaises(ZpValidationError) as ctx:
            validate_zp_payload(invalid)
        self.assertIn('Neispravan EU PDV ID', str(ctx.exception))
