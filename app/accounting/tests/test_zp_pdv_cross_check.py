"""Cross-form CI: build_zp_payload vs aggregate_vat_boxes boxes 101/103.

Uses the same VAT ledger as PDV aggregation — does NOT assert build_pdv_payload()
field_scalar('101'/'103') which remain ZERO until a future PDV mapping sprint.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from accounting.models import VATPeriod
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes
from accounting.services.tax_forms.zp.build import build_zp_payload
from accounting.services.tax_forms.zp.verify import verify_zp_against_pdv_boxes
from accounting.tests.fixtures.tax.zp.load import load_scenario
from accounting.tests.test_zp_aggregate import _seed_ledger
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant

class ZpPdvCrossCheckTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='zp-pdv-cross', name='ZP PDV Cross Co')
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

    def _verify_scenario(self, scenario_id: str, *, ledger_key: str = 'ledger') -> None:
        data = load_scenario(scenario_id)
        ledger = data[ledger_key]
        period_info = ledger['period']
        period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )
        _seed_ledger(tenant=self.tenant, period=period, ledger=ledger)

        boxes = aggregate_vat_boxes(period)
        zp = build_zp_payload(period)
        pdv = data['pdv']

        if scenario_id == 'pdv_mismatch_101_103':
            aligned = verify_zp_against_pdv_boxes(
                zp,
                pdv_box_101=boxes['101'].base,
                pdv_box_103=boxes['103'].base,
            )
            self.assertTrue(aligned.is_aligned)

            mismatched = verify_zp_against_pdv_boxes(
                zp,
                pdv_box_101=Decimal(pdv['fields']['101']),
                pdv_box_103=Decimal(pdv['fields']['103']),
            )
            self.assertEqual(mismatched.is_aligned, pdv['must_match_zp_totals'])
            return

        result = verify_zp_against_pdv_boxes(
            zp,
            pdv_box_101=boxes['101'].base,
            pdv_box_103=boxes['103'].base,
        )
        self.assertEqual(result.is_aligned, pdv['must_match_zp_totals'])

    def test_single_eu_partner(self):
        self._verify_scenario('single_eu_partner')

    def test_multiple_eu_partners(self):
        self._verify_scenario('multiple_eu_partners')

    def test_empty_period(self):
        self._verify_scenario('empty_period')

    def test_pdv_mismatch_101_103(self):
        self._verify_scenario('pdv_mismatch_101_103')

    def test_period_correction_v1(self):
        self._verify_scenario('period_correction', ledger_key='ledger_v1')

    def test_period_correction_v2(self):
        self._verify_scenario('period_correction', ledger_key='ledger_v2')
