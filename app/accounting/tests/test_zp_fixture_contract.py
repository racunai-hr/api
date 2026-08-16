"""Contract tests for ZP fixture files — no ZP implementation required."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from accounting.tests.fixtures.tax.zp.load import SCENARIO_IDS, ZP_FIXTURES_ROOT, load_scenario

_REQUIRED_LEDGER_KEYS = {'period', 'entries'}
_REQUIRED_ENTRY_KEYS = {
    'ledger_type',
    'vat_box',
    'entry_date',
    'net_amount',
    'partner_country',
    'partner_vat_id',
}
_REQUIRED_PAYLOAD_KEYS = {
    'schema_version',
    'mapping_version',
    'period_from',
    'period_to',
    'rows',
}
_REQUIRED_ROW_KEYS = {'country_code', 'pdv_id', 'goods_value', 'services_value'}


def _sum_rows_goods_services(rows: list[dict]) -> tuple[Decimal, Decimal]:
    goods = sum((Decimal(row['goods_value']) for row in rows), Decimal('0.00'))
    services = sum((Decimal(row['services_value']) for row in rows), Decimal('0.00'))
    return goods, services


class ZpFixtureContractTests(SimpleTestCase):
    def test_all_scenarios_exist_on_disk(self):
        for scenario_id in SCENARIO_IDS:
            self.assertTrue((ZP_FIXTURES_ROOT / scenario_id).is_dir(), scenario_id)

    def test_single_eu_partner_structure(self):
        data = load_scenario('single_eu_partner')
        ledger = data['ledger']
        self.assertEqual(_REQUIRED_LEDGER_KEYS, _REQUIRED_LEDGER_KEYS & ledger.keys())
        self.assertEqual(len(ledger['entries']), 1)
        self.assertEqual(_REQUIRED_ENTRY_KEYS, _REQUIRED_ENTRY_KEYS & ledger['entries'][0].keys())

        expected = data['expected']
        self.assertEqual(_REQUIRED_PAYLOAD_KEYS, _REQUIRED_PAYLOAD_KEYS & expected.keys())
        self.assertEqual(len(expected['rows']), 1)

        goods, services = _sum_rows_goods_services(expected['rows'])
        self.assertEqual(goods, Decimal('15000.00'))
        self.assertEqual(services, Decimal('0.00'))
        self.assertEqual(Decimal(data['pdv']['fields']['101']), goods)
        self.assertEqual(Decimal(data['pdv']['fields']['103']), services)

    def test_multiple_eu_partners_totals(self):
        data = load_scenario('multiple_eu_partners')
        goods, services = _sum_rows_goods_services(data['expected']['rows'])
        self.assertEqual(goods, Decimal('20000.00'))
        self.assertEqual(services, Decimal('6700.50'))
        self.assertTrue(data['pdv']['must_match_zp_totals'])

    def test_empty_period(self):
        data = load_scenario('empty_period')
        self.assertEqual(data['ledger']['entries'], [])
        self.assertEqual(data['expected']['rows'], [])

    def test_invalid_vat_id_expects_validation_error(self):
        data = load_scenario('invalid_vat_id')
        self.assertNotIn('expected', data)
        self.assertTrue(data['validation']['expect_error'])

    def test_pdv_mismatch_scenario(self):
        data = load_scenario('pdv_mismatch_101_103')
        self.assertFalse(data['pdv']['must_match_zp_totals'])
        goods, services = _sum_rows_goods_services(data['expected']['rows'])
        self.assertNotEqual(Decimal(data['pdv']['fields']['101']), goods)

    def test_period_correction_versions(self):
        data = load_scenario('period_correction')
        v1_goods, _ = _sum_rows_goods_services(data['expected_v1']['rows'])
        v2_goods, _ = _sum_rows_goods_services(data['expected_v2']['rows'])
        self.assertEqual(v1_goods, Decimal('20000.00'))
        self.assertEqual(v2_goods, Decimal('15000.00'))
        self.assertEqual(data['submission']['submission_flow']['v2']['submission_type'], 'correction')
