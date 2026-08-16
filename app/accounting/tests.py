from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounting.admin import DocumentTypeFilter, JournalEntryLineAdmin
from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, PostingRule, RRIFChartEntry
from accounting.rrif import enrich_rrif_rows, find_parent_code, load_rrif_rows
from accounting.services.chart import provision_tenant_chart
from accounting.services.journal_markers import (
    build_document_marker,
    document_type_label,
    extract_document_type,
    has_document_marker,
    iter_document_types,
)
from accounting.services.posting import invoice_payment_marker, post_document, post_invoice_payment
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.vat import aggregate_vat_period, generate_vat_ledger
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from payments.models import BankAccount, Payment
from tenants.models import Tenant


class RRIFImportTests(TestCase):
    def test_load_and_enrich_rows(self):
        rows = enrich_rrif_rows(load_rrif_rows())
        self.assertEqual(len(rows), 2472)
        self.assertEqual(find_parent_code('1201', {r['code'] for r in rows}), '120')

    def test_import_rrif_chart(self):
        created, updated = import_rrif_chart(clear=True)
        count = RRIFChartEntry.objects.count()
        self.assertGreaterEqual(count, 2460)
        self.assertLessEqual(count, 2472)


class PostingTests(TestCase):
    def setUp(self):
        import_rrif_chart(clear=True)
        self.tenant = Tenant.objects.create(slug='testco', name='Test Co')
        provision_tenant_chart(self.tenant)
        User = get_user_model()
        self.user = User.objects.create_user(username='acc', password='test')
        self.partner = Partner.all_objects.create(
            tenant=self.tenant,
            name='Kupac d.o.o.',
            tax_number='12345678901',
            partner_type='customer',
            status='active',
            address='Ulica 1',
            city='Šibenik',
            postal_code='22000',
        )
        self.invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.partner,
            issue_date=date(2026, 5, 15),
            due_date=date(2026, 5, 30),
            status='sent',
            subtotal=Decimal('100.00'),
            tax_amount=Decimal('25.00'),
            total_amount=Decimal('125.00'),
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=self.invoice,
            item_name='Usluga',
            quantity=1,
            unit_price=Decimal('100.00'),
            tax_rate=Decimal('25.00'),
        )

    def test_post_invoice_balanced(self):
        entry = post_document(self.tenant, self.invoice, 'invoice_issued', self.user)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.total_debit, entry.total_credit)
        self.assertEqual(entry.status, 'posted')

    def _create_payment(self, amount, payment_date, payment_number='PAY-001'):
        bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni EUR',
            bank_name='OTP',
            account_number='HR6124070001100204771',
            iban='HR6124070001100204771',
        )
        return Payment.all_objects.create(
            tenant=self.tenant,
            payment_number=payment_number,
            payment_type='incoming',
            payment_method='bank_transfer',
            status='completed',
            amount=Decimal(str(amount)),
            bank_account=bank_account,
            related_invoice=self.invoice,
            payment_date=payment_date,
            created_by=self.user,
        )

    def test_post_invoice_payment_creates_balanced_entry(self):
        payment = self._create_payment('500.00', date(2026, 5, 18))
        entry = post_invoice_payment(self.tenant, self.invoice, payment, self.user)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status, 'posted')
        self.assertEqual(entry.entry_date, date(2026, 5, 18))
        self.assertEqual(entry.reference, 'PAY-001')
        self.assertTrue(entry.description.startswith(invoice_payment_marker(payment)))
        self.assertEqual(entry.total_debit, Decimal('500.00'))
        self.assertEqual(entry.total_credit, Decimal('500.00'))

        lines = list(entry.lines.select_related('account', 'analytic_account').all())
        debit_line = next(line for line in lines if line.debit_amount > 0)
        credit_line = next(line for line in lines if line.credit_amount > 0)
        self.assertEqual(debit_line.account.account_code, '1000')
        self.assertEqual(debit_line.debit_amount, Decimal('500.00'))
        self.assertTrue(credit_line.analytic_account.account_code.startswith('1201-P'))
        self.assertEqual(credit_line.credit_amount, Decimal('500.00'))

    def test_post_invoice_payment_idempotent(self):
        payment = self._create_payment('500.00', date(2026, 5, 18), payment_number='PAY-002')
        first = post_invoice_payment(self.tenant, self.invoice, payment, self.user)
        second = post_invoice_payment(self.tenant, self.invoice, payment, self.user)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_vat_ledger_generation(self):
        from accounting.models import VATLedgerEntry

        generate_vat_ledger(self.tenant, 2026, 5, replace=True)
        period = aggregate_vat_period(
            __import__('accounting.models', fromlist=['VATPeriod']).VATPeriod.all_objects.get(
                tenant=self.tenant, year=2026, month=5,
            )
        )
        self.assertGreater(period['total_output_vat'], Decimal('0'))
        self.assertGreater(period['i_ra_count'], 0)
        output_type = VATLedgerEntry.all_objects.filter(
            tenant=self.tenant,
            vat_period__year=2026,
            vat_period__month=5,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
        ).exists()
        self.assertTrue(output_type)


class ReportTests(PostingTests):
    def test_trial_balance_after_posting(self):
        from accounting.services.posting import post_document
        from accounting.services.reports import trial_balance

        post_document(self.tenant, self.invoice, 'invoice_issued', self.user)
        rows = trial_balance(self.tenant, 2026, 5)
        self.assertTrue(len(rows) > 0)
        total_debit = sum(r['debit'] for r in rows)
        total_credit = sum(r['credit'] for r in rows)
        self.assertEqual(total_debit, total_credit)

    def test_bilanca_and_rdg_export(self):
        from accounting.services.posting import post_document
        from accounting.services.reports import balance_sheet, export_bilanca_xlsx, export_rdg_xlsx, income_statement

        post_document(self.tenant, self.invoice, 'invoice_issued', self.user)
        bilanca = balance_sheet(self.tenant, 2026, 5)
        rdg = income_statement(self.tenant, 2026, 5)
        self.assertIn('total_aktiva', bilanca)
        self.assertIn('rezultat', rdg)
        xlsx_b = export_bilanca_xlsx(self.tenant, 2026, 5)
        xlsx_r = export_rdg_xlsx(self.tenant, 2026, 5)
        self.assertTrue(xlsx_b[:2] == b'PK')
        self.assertTrue(xlsx_r[:2] == b'PK')


class ReportingStornoTests(PostingTests):
    def test_net_trial_balance_excludes_storno_pairs(self):
        from accounting.services.posting import post_document
        from accounting.services.reports import trial_balance

        post_document(self.tenant, self.invoice, 'invoice_issued', self.user)
        original = JournalEntry.objects.filter(
            tenant=self.tenant,
            description__contains='[invoice_issued]',
        ).first()
        self.assertIsNotNone(original)
        original.reverse(self.user)

        rows = trial_balance(self.tenant, 2026, 5)
        total_debit = sum(r['debit'] for r in rows)
        total_credit = sum(r['credit'] for r in rows)
        self.assertEqual(total_debit, Decimal('0'))
        self.assertEqual(total_credit, Decimal('0'))

    def test_journal_report_includes_storno_pairs_with_labels(self):
        from accounting.reporting.query import EntryAuditKind, entry_audit_kind
        from accounting.services.posting import post_document
        from accounting.services.reports import journal_report

        post_document(self.tenant, self.invoice, 'invoice_issued', self.user)
        original = JournalEntry.objects.filter(
            tenant=self.tenant,
            description__contains='[invoice_issued]',
        ).first()
        reversal = original.reverse(self.user)

        self.assertEqual(entry_audit_kind(original), EntryAuditKind.REVERSED)
        self.assertEqual(entry_audit_kind(reversal), EntryAuditKind.STORNO)

        reversed_items = journal_report(self.tenant, original.entry_date.year, original.entry_date.month)
        reversed_kinds = {item.audit_kind for item in reversed_items}
        self.assertIn(EntryAuditKind.REVERSED, reversed_kinds)
        self.assertIn('Stornirano (original)', {item.audit_label for item in reversed_items})

        storno_items = journal_report(self.tenant, reversal.entry_date.year, reversal.entry_date.month)
        storno_kinds = {item.audit_kind for item in storno_items}
        self.assertIn(EntryAuditKind.STORNO, storno_kinds)
        self.assertIn('Storno', {item.audit_label for item in storno_items})


class JournalMarkerTests(TestCase):
    def test_has_document_marker(self):
        self.assertTrue(has_document_marker('[expense_paid] Plaćanje računa'))
        self.assertFalse(has_document_marker('Ručna temeljnica'))

    def test_extract_document_type(self):
        self.assertEqual(extract_document_type('[expense_paid] Plaćanje računa'), 'expense_paid')
        self.assertEqual(extract_document_type('[EXPENSE_PAID] Plaćanje'), 'expense_paid')
        self.assertIsNone(extract_document_type('Ručna temeljnica'))
        self.assertIsNone(extract_document_type('[unknown_type] test'))

    def test_iter_document_types(self):
        types = list(iter_document_types())
        self.assertEqual(types, PostingRule.DOCUMENT_TYPES)

    def test_build_document_marker(self):
        self.assertEqual(build_document_marker('expense_paid'), '[expense_paid]')

    def test_document_type_label(self):
        self.assertEqual(document_type_label('[expense_paid] test'), 'Plaćen trošak')
        self.assertEqual(document_type_label('Ručna temeljnica'), 'Ručno / ostalo')


class DocumentTypeFilterTests(TestCase):
    def setUp(self):
        import_rrif_chart(clear=True)
        self.tenant = Tenant.objects.create(slug='filterco', name='Filter Co')
        provision_tenant_chart(self.tenant)
        User = get_user_model()
        self.user = User.objects.create_user(username='filter', password='test')
        self.account = ChartOfAccounts.all_objects.filter(tenant=self.tenant, is_postable=True).first()
        self.line_paid = self._create_line('202606-0001', '[expense_paid] Plaćen trošak')
        self.line_approved = self._create_line('202606-0002', '[expense_approved] Odobren trošak')
        self.line_manual = self._create_line('202606-0003', 'Ručna temeljnica')
        self.admin_instance = JournalEntryLineAdmin(JournalEntryLine, admin.site)
        self.request = RequestFactory().get('/')

    def _create_line(self, entry_number, description):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number=entry_number,
            entry_date=date(2026, 6, 4),
            status='posted',
            description=description,
            created_by=self.user,
        )
        return JournalEntryLine.objects.create(
            journal_entry=entry,
            account=self.account,
            debit_amount=Decimal('100.00'),
        )

    def _filter(self, value):
        filt = DocumentTypeFilter(self.request, {'document_type': value}, JournalEntryLine, self.admin_instance)
        return filt.queryset(self.request, JournalEntryLine.objects.all())

    def test_filter_expense_paid(self):
        qs = self._filter('expense_paid')
        self.assertEqual(set(qs), {self.line_paid})

    def test_filter_expense_approved(self):
        qs = self._filter('expense_approved')
        self.assertEqual(set(qs), {self.line_approved})

    def test_filter_manual(self):
        qs = self._filter('manual')
        self.assertEqual(set(qs), {self.line_manual})

    def test_entry_number_link(self):
        url = self.admin_instance.entry_number_link(self.line_paid)
        expected = reverse('admin:accounting_journalentry_change', args=[self.line_paid.journal_entry_id])
        self.assertIn(expected, url)
        self.assertIn(self.line_paid.journal_entry.entry_number, url)
