"""Performance smoke tests for the PDV pipeline (stabilization baseline)."""

import calendar
import time
from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounting.models import VATLedgerEntry, VATPeriod
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from accounting.services.tax_forms.pdv.payload import PdvFormHeader
from accounting.services.tax_forms.pdv.render import render_pdv_obrazac_xml
from accounting.services.vat import generate_vat_ledger
from settings.models import CompanySettings, TaxOffice
from tenants.models import Tenant

_LEDGER_SECONDS = 5.0
_AGGREGATE_SECONDS = 0.5
_BUILD_SECONDS = 0.1
_RENDER_SECONDS = 0.5
_PIPELINE_SECONDS = 10.0

_ENTRY_COUNT = 500


class PdvPerformanceTests(TestCase):
    """Smoke benchmarks for ledger → aggregate → build → render."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='pdv-perf', name='PDV perf tenant')
        tax_office = TaxOffice.objects.create(code='9999', name='Test PU', city='Zagreb')
        CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Perf d.o.o.',
            company_address='Ulica 1\n10000 Zagreb',
            street='Ulica',
            house_number='1',
            postal_code='10000',
            city='Zagreb',
            company_phone='+385 1 000 000',
            company_email='perf@example.com',
            vat_number='98765432109',
            tax_office=tax_office,
        )
        cls.period = VATPeriod.all_objects.create(tenant=cls.tenant, year=2026, month=6)
        entry_date = date(2026, 6, 15)

        entries = []
        for i in range(_ENTRY_COUNT):
            box = ('203', '303')[i % 2]
            entries.append(
                VATLedgerEntry(
                    tenant=cls.tenant,
                    vat_period=cls.period,
                    ledger_type=VATLedgerEntry.LEDGER_I_RA if box == '203' else VATLedgerEntry.LEDGER_U_RA,
                    entry_date=entry_date,
                    document_number=f'PERF-{i:04d}',
                    partner_name='Partner',
                    base_amount=Decimal('100.00'),
                    vat_rate=Decimal('25.00'),
                    vat_amount=Decimal('25.00'),
                    vat_box=box,
                )
            )
        VATLedgerEntry.all_objects.bulk_create(entries)

    def _assert_under(self, label: str, elapsed: float, limit: float):
        self.assertLess(
            elapsed,
            limit,
            f'{label} took {elapsed:.3f}s (limit {limit}s)',
        )

    def test_aggregate_and_build_under_baseline(self):
        t0 = time.perf_counter()
        boxes = aggregate_vat_boxes(self.period)
        agg_elapsed = time.perf_counter() - t0
        self._assert_under('aggregate_vat_boxes', agg_elapsed, _AGGREGATE_SECONDS)
        self.assertEqual(boxes['203'].vat, Decimal('6250.00'))

        t0 = time.perf_counter()
        payload = build_pdv_payload(self.period)
        build_elapsed = time.perf_counter() - t0
        self._assert_under('build_pdv_payload', build_elapsed, _BUILD_SECONDS)
        self.assertIsNotNone(payload.field_scalar('400'))

    def test_render_under_baseline(self):
        payload = build_pdv_payload(self.period)
        header = PdvFormHeader(tax_office_code='9999', prepared_by_first_name='Test', prepared_by_last_name='User')
        t0 = time.perf_counter()
        xml = render_pdv_obrazac_xml(payload, header=header)
        render_elapsed = time.perf_counter() - t0
        self._assert_under('render_pdv_obrazac_xml', render_elapsed, _RENDER_SECONDS)
        self.assertGreater(len(xml), 1000)

    def test_generate_vat_ledger_under_baseline(self):
        VATLedgerEntry.all_objects.filter(vat_period=self.period).delete()
        t0 = time.perf_counter()
        created, total = generate_vat_ledger(self.tenant, 2026, 6, replace=True)
        ledger_elapsed = time.perf_counter() - t0
        self._assert_under('generate_vat_ledger (empty period)', ledger_elapsed, _LEDGER_SECONDS)
        self.assertEqual(created, 0)

    def test_full_pipeline_under_baseline(self):
        t0 = time.perf_counter()
        aggregate_vat_boxes(self.period)
        payload = build_pdv_payload(self.period)
        header = PdvFormHeader(tax_office_code='9999', prepared_by_first_name='T', prepared_by_last_name='U')
        render_pdv_obrazac_xml(payload, header=header)
        total_elapsed = time.perf_counter() - t0
        self._assert_under('full pipeline (aggregate+build+render)', total_elapsed, _PIPELINE_SECONDS)
