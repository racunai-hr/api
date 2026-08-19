"""Reversal relevance assessment — adapter facts for PDV prepare."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from accounting.models import JournalEntry, JournalEntryLine, VATPeriod
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.tax_shadow.adapters import adapt_reversal_entry
from accounting.services.tax_shadow.reversal_relevance import assess_reversal_relevance
from domains.tax.classification.contracts import OriginTaxOwner, Outcome, TaxRelevance
from domains.tax.classification.engine import classify
from invoices.models import Invoice
from partners.models import Partner
from tenants.models import Tenant


class ReversalRelevanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='revrel', name='Rev Rel')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='revrel', password='test')
        cls.partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Kupac',
            tax_number='12345678901',
            partner_type='customer',
            status='active',
            address='Ulica 1',
            city='Zagreb',
            postal_code='10000',
        )
        cls.invoice_ct = ContentType.objects.get_for_model(Invoice)
        cls.period = VATPeriod.all_objects.create(
            tenant=cls.tenant, year=2026, month=7, status='open',
        )

    def _account(self, code):
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def _invoice(self, *, status='paid'):
        return Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.partner,
            issue_date=date(2026, 7, 3),
            due_date=date(2026, 7, 20),
            status=status,
            invoice_number='2026-T001',
            subtotal=Decimal('2560.00'),
            tax_amount=Decimal('640.00'),
            total_amount=Decimal('3200.00'),
            created_by=self.user,
        )

    def test_invoice_paid_accrual_not_tax_relevant(self):
        invoice = self._invoice()
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202607-paid',
            entry_date=date(2026, 7, 3),
            status='posted',
            description='[invoice_paid] 2026-T001 - 1600 EUR',
            created_by=self.user,
            source_content_type=self.invoice_ct,
            source_object_id=invoice.pk,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1000'), debit_amount=Decimal('1600.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1201'), credit_amount=Decimal('1600.00'),
        )
        assessment = assess_reversal_relevance(entry, effects_present=False, effects_ambiguous=False)
        self.assertEqual(assessment.tax_relevance, TaxRelevance.NOT_TAX_RELEVANT)
        reversal = entry.reverse(self.user)
        document = adapt_reversal_entry(reversal, period=self.period)
        result = classify(document)
        self.assertEqual(result.outcome, Outcome.NOT_TAX_RELEVANT)
        self.assertEqual(result.rule_code, 'REV_NOT_TAX_RELEVANT')
        self.assertEqual(result.rows, ())

    def test_invoice_paid_cash_accounting_not_automatic_ntr(self):
        invoice = self._invoice()
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202607-cash',
            entry_date=date(2026, 7, 3),
            status='posted',
            description='[invoice_paid] cash',
            created_by=self.user,
            source_content_type=self.invoice_ct,
            source_object_id=invoice.pk,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1000'), debit_amount=Decimal('100.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1201'), credit_amount=Decimal('100.00'),
        )
        with patch(
            'accounting.services.tax_shadow.reversal_relevance.tenant_uses_cash_accounting',
            return_value=True,
        ):
            assessment = assess_reversal_relevance(entry, effects_present=False, effects_ambiguous=False)
        self.assertEqual(assessment.tax_relevance, TaxRelevance.UNDETERMINED)

    def test_invoice_issued_active_without_line_evidence_undetermined(self):
        invoice = self._invoice(status='paid')
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202607-iss',
            entry_date=date(2026, 7, 3),
            status='posted',
            description='[invoice_issued] 2026-T001 - 1600 EUR',
            created_by=self.user,
            source_content_type=self.invoice_ct,
            source_object_id=invoice.pk,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1201'), debit_amount=Decimal('1280.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('7510'), credit_amount=Decimal('1280.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1201'), debit_amount=Decimal('320.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('24001'), credit_amount=Decimal('320.00'),
        )
        assessment = assess_reversal_relevance(entry, effects_present=False, effects_ambiguous=False)
        self.assertEqual(assessment.tax_relevance, TaxRelevance.UNDETERMINED)
        self.assertEqual(assessment.origin_tax_owner, OriginTaxOwner.INVOICE)
        reversal = entry.reverse(self.user)
        document = adapt_reversal_entry(reversal, period=self.period)
        result = classify(document)
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)
        self.assertEqual(result.rule_code, 'REVERSAL_TAX_OWNER_UNDETERMINED')
        self.assertEqual(result.rows, ())

    def test_marker_and_accounts_conflict_undetermined(self):
        invoice = self._invoice()
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202607-conf',
            entry_date=date(2026, 7, 3),
            status='posted',
            description='[invoice_paid] conflict',
            created_by=self.user,
            source_content_type=self.invoice_ct,
            source_object_id=invoice.pk,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1400'), debit_amount=Decimal('25.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('2201'), credit_amount=Decimal('25.00'),
        )
        assessment = assess_reversal_relevance(entry, effects_present=False, effects_ambiguous=False)
        self.assertEqual(assessment.tax_relevance, TaxRelevance.UNDETERMINED)

    def test_ppmv_without_extra_pdv_account_ntr(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202607-ppmv',
            entry_date=date(2026, 6, 19),
            status='posted',
            description='PPMV — VW Golf',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('0373'), debit_amount=Decimal('1051.04'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('1000'), credit_amount=Decimal('1051.04'),
        )
        assessment = assess_reversal_relevance(entry, effects_present=False, effects_ambiguous=False)
        self.assertEqual(assessment.tax_relevance, TaxRelevance.NOT_TAX_RELEVANT)
        reversal = entry.reverse(self.user)
        period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=6, status='open')
        document = adapt_reversal_entry(reversal, period=period)
        result = classify(document)
        self.assertEqual(result.outcome, Outcome.NOT_TAX_RELEVANT)

    def test_ppmv_with_unexpected_pdv_account_fail_closed(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202607-ppmv-bad',
            entry_date=date(2026, 6, 19),
            status='posted',
            description='PPMV with VAT',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('0373'), debit_amount=Decimal('100.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry, account=self._account('24001'), credit_amount=Decimal('100.00'),
        )
        assessment = assess_reversal_relevance(entry, effects_present=False, effects_ambiguous=False)
        self.assertNotEqual(assessment.tax_relevance, TaxRelevance.NOT_TAX_RELEVANT)

    def test_je_line_effects_remain_tax_relevant(self):
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202607-keep',
            entry_date=date(2026, 7, 1),
            status='posted',
            description='Pretporez',
            created_by=self.user,
        )
        assessment = assess_reversal_relevance(entry, effects_present=True, effects_ambiguous=False)
        self.assertEqual(assessment.tax_relevance, TaxRelevance.TAX_RELEVANT)
        self.assertEqual(assessment.origin_tax_owner, OriginTaxOwner.JOURNAL_LINE)
