"""Property tests for EU box invariants (207/307)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes, compute_vat_due
from accounting.services.vat import generate_vat_ledger
from tenants.models import Tenant


class PdvEuInvariantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='eu-invariants', name='EU Invariants Co')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='eu-inv', password='test')

    def _account(self, code: str) -> ChartOfAccounts:
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def _seed_eu_goods(self, base: Decimal = Decimal('8000.00')):
        acquisition = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0200',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='EU stjecanje',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('0373'),
            debit_amount=base,
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('1000'),
            credit_amount=base,
        )
        vat = (base * Decimal('0.25')).quantize(Decimal('0.01'))
        reverse_charge = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0201',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='RC EU',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=reverse_charge,
            account=self._account('14022'),
            debit_amount=vat,
        )
        JournalEntryLine.objects.create(
            journal_entry=reverse_charge,
            account=self._account('24022'),
            credit_amount=vat,
        )
        return base, vat

    def test_invariants_i1_to_i3(self):
        base, vat = self._seed_eu_goods()
        generate_vat_ledger(self.tenant, 2026, 5, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=5)
        boxes = aggregate_vat_boxes(period)

        self.assertGreaterEqual(boxes['207'].base, Decimal('0.00'))
        self.assertEqual(boxes['207'].vat, (base * Decimal('0.25')).quantize(Decimal('0.01')))
        self.assertEqual(boxes['207'].vat, boxes['307'].vat)
        self.assertEqual(boxes['207'].base, boxes['307'].base)

    def test_invariant_i4_vat_due_unchanged_when_eu_neutral(self):
        base, _vat = self._seed_eu_goods()
        generate_vat_ledger(self.tenant, 2026, 5, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=5)
        boxes_with_eu = aggregate_vat_boxes(period)
        vat_due_with_eu = compute_vat_due(boxes_with_eu)

        zero_eu = dict(boxes_with_eu)
        for code in ('207', '307'):
            zero_eu[code] = type(boxes_with_eu[code])(Decimal('0.00'), Decimal('0.00'))

        self.assertEqual(vat_due_with_eu, compute_vat_due(zero_eu))
        self.assertEqual(boxes_with_eu['207'].base, base)
