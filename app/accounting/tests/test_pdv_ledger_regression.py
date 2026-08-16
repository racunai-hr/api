"""Regression tests for generate_vat_ledger v2."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, VATLedgerEntry, VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes
from accounting.services.vat import generate_vat_ledger
from expenses.models import Expense, ExpenseCategory
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from tenants.models import Tenant


class PdvLedgerRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='ledgerco', name='Ledger Co')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='ledger', password='test')
        cls.partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Kupac d.o.o.',
            tax_number='12345678901',
            partner_type='customer',
            status='active',
            address='Ulica 1',
            city='Šibenik',
            postal_code='22000',
        )
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljač d.o.o.',
            tax_number='98765432109',
            partner_type='supplier',
            status='active',
            address='Ulica 1',
            city='Zagreb',
            postal_code='10000',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def _account(self, code):
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def _seed_april_documents(self):
        invoice_25 = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.partner,
            issue_date=date(2026, 4, 5),
            due_date=date(2026, 4, 20),
            status='sent',
            subtotal=Decimal('100.00'),
            tax_amount=Decimal('25.00'),
            total_amount=Decimal('125.00'),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice_25,
            item_name='Usluga 25%',
            quantity=1,
            unit_price=Decimal('100.00'),
            tax_rate=Decimal('25.00'),
        )

        invoice_13 = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.partner,
            issue_date=date(2026, 4, 8),
            due_date=date(2026, 4, 23),
            status='sent',
            subtotal=Decimal('200.00'),
            tax_amount=Decimal('26.00'),
            total_amount=Decimal('226.00'),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice_13,
            item_name='Usluga 13%',
            quantity=1,
            unit_price=Decimal('200.00'),
            tax_rate=Decimal('13.00'),
        )

        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='EXP-2026-001',
            status='approved',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('420.43'),
            tax_amount=Decimal('84.11'),
            expense_date=date(2026, 4, 12),
            description='Trošak s pretporezom',
            created_by=self.user,
        )

        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202604-0001',
            entry_date=date(2026, 4, 15),
            status='posted',
            description='Ručno knjiženje pretporeza',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=self._account('1400'),
            debit_amount=Decimal('10.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=self._account('2201'),
            credit_amount=Decimal('10.00'),
        )

    def test_generate_vat_ledger_assigns_vat_boxes(self):
        self._seed_april_documents()
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)

        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        boxes = aggregate_vat_boxes(period)

        self.assertEqual(boxes['203'].base, Decimal('100.00'))
        self.assertEqual(boxes['203'].vat, Decimal('25.00'))
        self.assertEqual(boxes['202'].base, Decimal('200.00'))
        self.assertEqual(boxes['202'].vat, Decimal('26.00'))
        self.assertEqual(boxes['303'].base, Decimal('376.32'))
        self.assertEqual(boxes['303'].vat, Decimal('94.11'))

    def test_replace_preserves_manual_entries(self):
        period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=5)
        manual = VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=period,
            ledger_type=VATLedgerEntry.LEDGER_U_RA,
            entry_date=date(2026, 5, 1),
            document_number='MANUAL-1',
            partner_name='Ručno',
            partner_oib='',
            base_amount=Decimal('10.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('2.50'),
            vat_box='303',
            is_manual=True,
        )

        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.partner,
            issue_date=date(2026, 5, 10),
            due_date=date(2026, 5, 25),
            status='sent',
            subtotal=Decimal('50.00'),
            tax_amount=Decimal('12.50'),
            total_amount=Decimal('62.50'),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Usluga',
            quantity=1,
            unit_price=Decimal('50.00'),
            tax_rate=Decimal('25.00'),
        )

        generate_vat_ledger(self.tenant, 2026, 5, replace=True)

        self.assertTrue(VATLedgerEntry.all_objects.filter(pk=manual.pk).exists())
        auto_entries = VATLedgerEntry.all_objects.filter(vat_period=period, is_manual=False)
        self.assertEqual(auto_entries.count(), 1)
        self.assertEqual(auto_entries.get().vat_box, '203')

    def test_regeneration_is_idempotent_for_same_sources(self):
        self._seed_april_documents()
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        first_totals = aggregate_vat_boxes(period)

        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        second_totals = aggregate_vat_boxes(period)

        self.assertEqual(first_totals, second_totals)
