"""Tests for verify_zp_period management command."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounting.models import VATPeriod
from accounting.services.tax_forms.zp.build import build_zp_payload
from accounting.services.tax_forms.zp.render import render_zp_xml
from accounting.tests.fixtures.tax.zp.load import load_scenario
from accounting.tests.test_zp_aggregate import _seed_ledger
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant


class VerifyZpPeriodCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='verify-zp-period', name='Verify ZP Co')
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

    def _seed_scenario(self, scenario_id: str) -> tuple[VATPeriod, int, int]:
        data = load_scenario(scenario_id)
        ledger = data['ledger']
        period_info = ledger['period']
        year = period_info['year']
        month = period_info['month']
        period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=year,
            month=month,
        )
        _seed_ledger(tenant=self.tenant, period=period, ledger=ledger)
        return period, year, month

    def test_success_without_xml(self):
        _, year, month = self._seed_scenario('single_eu_partner')
        call_command(
            'verify_zp_period',
            tenant=self.tenant.slug,
            year=year,
            month=month,
        )

    def test_success_with_matching_xml(self):
        _, year, month = self._seed_scenario('single_eu_partner')
        period = VATPeriod.all_objects.get(
            tenant=self.tenant,
            year=year,
            month=month,
        )
        payload = build_zp_payload(period)
        with tempfile.NamedTemporaryFile(suffix='.xml', delete=False) as tmp:
            tmp.write(render_zp_xml(payload))
            xml_path = tmp.name
        try:
            call_command(
                'verify_zp_period',
                tenant=self.tenant.slug,
                year=year,
                month=month,
                xml=xml_path,
            )
        finally:
            Path(xml_path).unlink(missing_ok=True)

    def test_fails_when_xml_missing(self):
        _, year, month = self._seed_scenario('single_eu_partner')
        with self.assertRaises(CommandError) as ctx:
            call_command(
                'verify_zp_period',
                tenant=self.tenant.slug,
                year=year,
                month=month,
                xml='/nonexistent/zp.xml',
            )
        self.assertIn('ne postoji', str(ctx.exception))

    def test_fails_on_payload_mismatch(self):
        _, year, month = self._seed_scenario('single_eu_partner')
        _, other_year, other_month = self._seed_scenario('multiple_eu_partners')
        other_period = VATPeriod.all_objects.get(
            tenant=self.tenant,
            year=other_year,
            month=other_month,
        )
        mismatched_xml = render_zp_xml(build_zp_payload(other_period))
        with tempfile.NamedTemporaryFile(suffix='.xml', delete=False) as tmp:
            tmp.write(mismatched_xml)
            xml_path = tmp.name
        try:
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    'verify_zp_period',
                    tenant=self.tenant.slug,
                    year=year,
                    month=month,
                    xml=xml_path,
                )
            self.assertIn('ERP != predani XML', str(ctx.exception))
        finally:
            Path(xml_path).unlink(missing_ok=True)
