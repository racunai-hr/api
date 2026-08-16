"""Tests for aggregate_vat_boxes."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounting.models import VATLedgerEntry, VATPeriod
from accounting.services.tax_forms.pdv.aggregate import BoxTotals, aggregate_vat_boxes
from accounting.services.tax_forms.pdv.boxes import VATBox
from tenants.models import Tenant


class PdvAggregateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='aggco', name='Aggregate Co')
        cls.period = VATPeriod.all_objects.create(tenant=cls.tenant, year=2026, month=4)

    def _create_entry(self, *, vat_box, base, vat, ledger_type=VATLedgerEntry.LEDGER_I_RA):
        VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=self.period,
            ledger_type=ledger_type,
            entry_date=date(2026, 4, 10),
            document_number=f'DOC-{vat_box}-{base}',
            partner_name='Partner',
            partner_oib='12345678901',
            base_amount=base,
            vat_rate=Decimal('25.00'),
            vat_amount=vat,
            vat_box=vat_box,
        )

    def test_aggregate_vat_boxes_sums_by_box(self):
        self._create_entry(vat_box=VATBox.BOX_203, base=Decimal('100.00'), vat=Decimal('25.00'))
        self._create_entry(vat_box=VATBox.BOX_203, base=Decimal('50.00'), vat=Decimal('12.50'))
        self._create_entry(
            vat_box=VATBox.BOX_303,
            base=Decimal('336.43'),
            vat=Decimal('84.11'),
            ledger_type=VATLedgerEntry.LEDGER_U_RA,
        )

        totals = aggregate_vat_boxes(self.period)

        self.assertEqual(
            totals['203'],
            BoxTotals(base=Decimal('150.00'), vat=Decimal('37.50')),
        )
        self.assertEqual(
            totals['303'],
            BoxTotals(base=Decimal('336.43'), vat=Decimal('84.11')),
        )
        self.assertEqual(
            totals['201'],
            BoxTotals(base=Decimal('0.00'), vat=Decimal('0.00')),
        )

    def test_aggregate_ignores_entries_without_vat_box(self):
        VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=self.period,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 4, 1),
            document_number='LEGACY',
            partner_name='Partner',
            partner_oib='',
            base_amount=Decimal('10.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('2.50'),
            vat_box='',
        )

        self.assertEqual(
            aggregate_vat_boxes(self.period)['201'],
            BoxTotals(base=Decimal('0.00'), vat=Decimal('0.00')),
        )
