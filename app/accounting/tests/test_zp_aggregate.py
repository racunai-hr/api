"""Tests for aggregate_zp_rows — fixture-driven."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounting.models import VATLedgerEntry, VATPeriod
from accounting.services.tax_forms.zp.aggregate import aggregate_zp_rows
from accounting.services.tax_forms.zp.canonical import payload_to_dict
from accounting.tests.fixtures.tax.zp.load import load_scenario
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant


def _seed_ledger(*, tenant, period: VATPeriod, ledger: dict) -> None:
    for idx, entry in enumerate(ledger['entries']):
        VATLedgerEntry.all_objects.create(
            tenant=tenant,
            vat_period=period,
            ledger_type=entry['ledger_type'],
            entry_date=date.fromisoformat(entry['entry_date']),
            document_number=f'ZP-FIX-{idx}',
            partner_name='EU Partner',
            partner_oib=entry['partner_vat_id'],
            base_amount=Decimal(entry['net_amount']),
            vat_rate=Decimal('0.00'),
            vat_amount=Decimal(entry.get('vat_amount', '0.00')),
            vat_box=entry['vat_box'],
        )


def _expected_aggregate_dict(expected: dict) -> dict:
    """Fields produced by aggregate_zp_rows (excludes version metadata)."""
    return {
        'schema_version': expected['schema_version'],
        'mapping_version': expected['mapping_version'],
        'period_from': expected['period_from'],
        'period_to': expected['period_to'],
        'taxpayer': expected['taxpayer'],
        'prepared_by': expected['prepared_by'],
        'tax_office_code': expected['tax_office_code'],
        'rows': expected['rows'],
    }


class ZpAggregateFixtureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='zp-aggregate', name='ZP Aggregate Co')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='FINE STAR D.O.O.',
            street='Bana Josipa Jelačića',
            house_number='58',
            postal_code='22000',
            city='Šibenik',
            vat_number='36619131370',
            tax_office=tax_office,
        )
        ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Toni',
            last_name='Šupe',
        )

    def _period_for_ledger(self, ledger: dict) -> VATPeriod:
        period_info = ledger['period']
        return VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )

    def _assert_matches_expected(self, period: VATPeriod, expected: dict) -> None:
        payload = aggregate_zp_rows(period)
        self.assertEqual(payload_to_dict(payload), _expected_aggregate_dict(expected))

    def test_single_eu_partner(self):
        data = load_scenario('single_eu_partner')
        period = self._period_for_ledger(data['ledger'])
        _seed_ledger(tenant=self.tenant, period=period, ledger=data['ledger'])
        self._assert_matches_expected(period, data['expected'])

    def test_multiple_eu_partners(self):
        data = load_scenario('multiple_eu_partners')
        period = self._period_for_ledger(data['ledger'])
        _seed_ledger(tenant=self.tenant, period=period, ledger=data['ledger'])
        self._assert_matches_expected(period, data['expected'])

    def test_empty_period(self):
        data = load_scenario('empty_period')
        period = self._period_for_ledger(data['ledger'])
        self._assert_matches_expected(period, data['expected'])

    def test_pdv_mismatch_scenario_payload(self):
        data = load_scenario('pdv_mismatch_101_103')
        period = self._period_for_ledger(data['ledger'])
        _seed_ledger(tenant=self.tenant, period=period, ledger=data['ledger'])
        self._assert_matches_expected(period, data['expected'])

    def test_period_correction_v1(self):
        data = load_scenario('period_correction')
        period = self._period_for_ledger(data['ledger_v1'])
        _seed_ledger(tenant=self.tenant, period=period, ledger=data['ledger_v1'])
        self._assert_matches_expected(period, data['expected_v1'])

    def test_period_correction_v2_includes_storno(self):
        data = load_scenario('period_correction')
        period = self._period_for_ledger(data['ledger_v2'])
        _seed_ledger(tenant=self.tenant, period=period, ledger=data['ledger_v2'])
        self._assert_matches_expected(period, data['expected_v2'])

    def test_invalid_vat_id_raises(self):
        data = load_scenario('invalid_vat_id')
        period = self._period_for_ledger(data['ledger'])
        _seed_ledger(tenant=self.tenant, period=period, ledger=data['ledger'])
        validation = data['validation']
        with self.assertRaises(ValueError) as ctx:
            aggregate_zp_rows(period)
        self.assertIn(validation['error_substring'], str(ctx.exception))
