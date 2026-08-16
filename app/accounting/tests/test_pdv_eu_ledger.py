"""EU ledger generation for boxes 207/307."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes
from accounting.services.vat import generate_vat_ledger
from expenses.models import Expense, ExpenseCategory
from partners.models import Partner
from tenants.models import Tenant


class PdvEuLedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='eu-ledger', name='EU Ledger Co')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='eu-ledger', password='test')
        cls.supplier_eu = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Automobile Hadžić',
            tax_number='DE229674882',
            partner_type='supplier',
            status='active',
            address='',
            city='',
            postal_code='',
            country='Germany',
        )
        cls.supplier_hr = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Domestic d.o.o.',
            tax_number='11111111111',
            partner_type='supplier',
            status='active',
            address='',
            city='',
            postal_code='',
            country='Croatia',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Materijal')

    def _account(self, code: str) -> ChartOfAccounts:
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def test_eu_goods_acquisition_from_journal_lines(self):
        acquisition = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0100',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='EU stjecanje T-Cross',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('0373'),
            debit_amount=Decimal('8000.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=acquisition,
            account=self._account('1000'),
            credit_amount=Decimal('8000.00'),
        )

        reverse_charge = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0101',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='RC EU stjecanje',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=reverse_charge,
            account=self._account('14022'),
            debit_amount=Decimal('2000.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=reverse_charge,
            account=self._account('24022'),
            credit_amount=Decimal('2000.00'),
        )

        generate_vat_ledger(self.tenant, 2026, 5, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=5)
        boxes = aggregate_vat_boxes(period)

        self.assertEqual(boxes['207'].base, Decimal('8000.00'))
        self.assertEqual(boxes['207'].vat, Decimal('2000.00'))
        self.assertEqual(boxes['307'].base, Decimal('8000.00'))
        self.assertEqual(boxes['307'].vat, Decimal('2000.00'))

    def test_eu_supplier_zero_vat_expense_excluded_from_303(self):
        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='EU-EXP-1',
            status='paid',
            category=self.category,
            supplier=self.supplier_eu,
            amount=Decimal('8000.00'),
            tax_amount=Decimal('0.00'),
            expense_date=date(2026, 5, 22),
            description='EU račun 0% PDV',
            created_by=self.user,
        )
        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='HR-EXP-1',
            status='paid',
            category=self.category,
            supplier=self.supplier_hr,
            amount=Decimal('39.90'),
            tax_amount=Decimal('7.98'),
            expense_date=date(2026, 5, 10),
            description='Domestic pretporez',
            created_by=self.user,
        )

        generate_vat_ledger(self.tenant, 2026, 5, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=5)
        boxes = aggregate_vat_boxes(period)

        self.assertEqual(boxes['303'].base, Decimal('31.92'))
        self.assertEqual(boxes['303'].vat, Decimal('7.98'))
        self.assertEqual(boxes['207'].base, Decimal('0.00'))
        self.assertEqual(boxes['614'].base, Decimal('8000.00'))
        self.assertEqual(boxes['610'].base, Decimal('8000.00'))
