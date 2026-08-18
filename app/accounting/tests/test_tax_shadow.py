"""Read-only shadow classification vs generate_vat_ledger."""

from datetime import date
from decimal import Decimal
from json import loads
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, VATLedgerEntry, VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_shadow.runner import ledger_fingerprint, shadow_classify_period
from accounting.services.vat import generate_vat_ledger
from domains.tax.classification.contracts import Outcome
from expenses.models import Expense, ExpenseCategory
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from tenants.models import Tenant

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'tax' / 'shadow'


class ShadowClassificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='shadowco', name='Shadow Co')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='shadow', password='test')
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
            country='Croatia',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def _account(self, code):
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def test_generic_expense_expected_divergence_and_fingerprint(self):
        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='EXP-2026-001',
            status='approved',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('125.00'),
            tax_amount=Decimal('25.00'),
            expense_date=date(2026, 4, 12),
            description='Generic pretporez',
            created_by=self.user,
        )
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        before = ledger_fingerprint(period)
        report = shadow_classify_period(period)
        self.assertEqual(report.fingerprint_before, before)
        self.assertEqual(report.fingerprint_after, before)
        self.assertFalse(report.stale)
        self.assertEqual(report.exit_code(), 0)
        codes = [item['code'] for item in report.expected]
        self.assertEqual(codes, ['EXPENSE_GENERIC_303_REMOVED'])
        self.assertEqual(report.unexpected, [])

    def test_two_runs_byte_identical_json(self):
        InvoiceItem.objects.create(
            invoice=Invoice.all_objects.create(
                tenant=self.tenant,
                company_to=self.partner,
                issue_date=date(2026, 4, 5),
                due_date=date(2026, 4, 20),
                status='sent',
                subtotal=Decimal('100.00'),
                tax_amount=Decimal('25.00'),
                total_amount=Decimal('125.00'),
                created_by=self.user,
            ),
            item_name='Usluga',
            quantity=1,
            unit_price=Decimal('100.00'),
            tax_rate=Decimal('25.00'),
        )
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        first = shadow_classify_period(period).to_json()
        second = shadow_classify_period(period).to_json()
        self.assertEqual(first, second)
        self.assertEqual(loads(first)['unexpected'], [])

    def test_duplicate_invoice_lines_multiplicity(self):
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.partner,
            issue_date=date(2026, 4, 6),
            due_date=date(2026, 4, 20),
            status='sent',
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice, item_name='A', quantity=1, unit_price=Decimal('50.00'), tax_rate=Decimal('25.00'),
        )
        InvoiceItem.objects.create(
            invoice=invoice, item_name='B', quantity=1, unit_price=Decimal('50.00'), tax_rate=Decimal('25.00'),
        )
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        report = shadow_classify_period(period)
        self.assertEqual(report.unexpected, [])
        self.assertEqual(report.exit_code(), 0)

    def test_reversal_without_ledger_evidence_invalid(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202604-rev-src',
            entry_date=date(2026, 4, 10),
            status='posted',
            description='Pretporez',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1400'), debit_amount=Decimal('25.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('2201'), credit_amount=Decimal('25.00'),
        )
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        reversal = entry.reverse(self.user)
        reversal.entry_date = date(2026, 4, 12)
        reversal.save(update_fields=['entry_date'])
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        report = shadow_classify_period(period)
        self.assertGreaterEqual(report.invalid, 1)
        self.assertNotEqual(report.exit_code(), 0)

    def test_reversal_restored_when_ledger_keeps_original(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202604-keep',
            entry_date=date(2026, 4, 11),
            status='posted',
            description='Pretporez keep',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1400'), debit_amount=Decimal('10.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('2201'), credit_amount=Decimal('10.00'),
        )
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        reversal = entry.reverse(self.user)
        reversal.entry_date = date(2026, 4, 12)
        reversal.save(update_fields=['entry_date'])
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        report = shadow_classify_period(period)
        codes = [item['code'] for item in report.expected]
        self.assertIn('REVERSAL_TAX_EFFECT_RESTORED', codes)

    def test_stale_run_not_mismatch(self):
        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='EXP-stale',
            status='approved',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('125.00'),
            tax_amount=Decimal('25.00'),
            expense_date=date(2026, 4, 12),
            description='stale',
            created_by=self.user,
        )
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        hashes = iter(['aaa', 'bbb'])
        with patch('accounting.services.tax_shadow.runner.input_snapshot_hash', side_effect=lambda _period: next(hashes)):
            report = shadow_classify_period(period)
        self.assertTrue(report.stale)
        self.assertEqual(report.exit_code(), 1)

    def test_cli_json_period_id(self):
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        call_command('shadow_vat_classification', period_id=period.pk, json=True)

    def test_fine_star_april_fixture_counts(self):
        expected = loads((_FIXTURES / 'april_2026_expected.json').read_text())
        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-2026-0007',
            status='paid',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('400.55'),
            tax_amount=Decimal('80.11'),
            expense_date=date(2026, 4, 24),
            receipt_number='865-PJ1-1',
            description='Trošak s pretporezom 25%',
            created_by=self.user,
        )
        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-2026-0008',
            status='paid',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('19.99'),
            tax_amount=Decimal('4.00'),
            expense_date=date(2026, 4, 16),
            receipt_number='476-37-1',
            description='Mali trošak s pretporezom',
            created_by=self.user,
        )
        generate_vat_ledger(self.tenant, 2026, 4, replace=True)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=4)
        report = shadow_classify_period(period)
        counts = {}
        for item in report.expected:
            counts[item['code']] = counts.get(item['code'], 0) + 1
        self.assertEqual(counts, expected['expected'])
        self.assertEqual(report.unexpected, [])


class ShadowMaySyntheticTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='shadow-may', name='Shadow May')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='shadow-may', password='test')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Domestic dobavljač',
            tax_number='11111111111',
            partner_type='supplier',
            status='active',
            address='',
            city='',
            postal_code='',
            country='Croatia',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Materijal')
        Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='MAY-303-1',
            status='paid',
            category=cls.category,
            supplier=cls.supplier,
            amount=Decimal('420.43'),
            tax_amount=Decimal('84.11'),
            expense_date=date(2026, 5, 12),
            description='Domestic pretporez',
            created_by=cls.user,
        )
        account = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='0373')
        bank = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='1000')
        pretporez = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='14022')
        obveza = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='24022')
        acquisition = JournalEntry.all_objects.create(
            tenant=cls.tenant,
            entry_number='202605-0011',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='EU stjecanje',
            created_by=cls.user,
        )
        JournalEntryLine.objects.create(journal_entry=acquisition, account=account, debit_amount=Decimal('8000.00'))
        JournalEntryLine.objects.create(journal_entry=acquisition, account=bank, credit_amount=Decimal('8000.00'))
        reverse_charge = JournalEntry.all_objects.create(
            tenant=cls.tenant,
            entry_number='202605-0012',
            entry_date=date(2026, 5, 22),
            status='posted',
            description='RC EU stjecanje',
            created_by=cls.user,
        )
        JournalEntryLine.objects.create(journal_entry=reverse_charge, account=pretporez, debit_amount=Decimal('2000.00'))
        JournalEntryLine.objects.create(journal_entry=reverse_charge, account=obveza, credit_amount=Decimal('2000.00'))
        generate_vat_ledger(cls.tenant, 2026, 5, replace=True)

    def test_may_fixture_and_rc_match(self):
        expected = loads((_FIXTURES / 'may_2026_expected.json').read_text())
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=5)
        report = shadow_classify_period(period)
        counts = {}
        for item in report.expected:
            counts[item['code']] = counts.get(item['code'], 0) + 1
        self.assertEqual(counts, expected['expected'])
        self.assertEqual(report.unexpected, [])
        self.assertEqual(report.exit_code(), 0)
        self.assertEqual(VATLedgerEntry.all_objects.filter(vat_period=period, vat_box='207').count(), 2)
