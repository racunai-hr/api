"""Tests for document_integrity read-only audit."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine, SubledgerAllocation, SubledgerItem
from accounting.services.chart import provision_tenant_chart
from domains.finance.services.document_integrity import (
    P1_CANCELLED_SUBLEDGER_PAID_DOC,
    P2_MANUAL_JE_NO_SUBLEDGER,
    P3_GL_BYPASS_AP_CANDIDATE,
    P5_ORPHAN_SUBLEDGER_WRONG_PARTNER,
    P6_BANK_SETTLED_NO_ALLOCATION,
    P8_STANDARD_PATH_OK,
    audit_tenant_document_mismatches,
    build_document_audit_record,
    classify_document_mismatch,
    collect_expense_posting_context,
)
from expenses.models import Expense, ExpenseCategory
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

HOST = 'docint.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class DocumentIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)

        cls.tenant = Tenant.objects.create(slug='docint', name='Doc Integrity Co')
        provision_tenant_chart(cls.tenant)

        User = get_user_model()
        cls.user = User.objects.create_user(username='docint-user', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='owner')

        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Test')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Supplier A',
            partner_type='supplier',
            status='active',
            tax_number='11111111111',
            address='A',
            city='Zagreb',
            postal_code='10000',
        )
        cls.other_partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Other Partner',
            partner_type='other',
            status='active',
            tax_number='22222222222',
            address='B',
            city='Zagreb',
            postal_code='10000',
        )

    def _account(self, code: str) -> ChartOfAccounts:
        return ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)

    def _create_expense(self, **kwargs) -> Expense:
        defaults = {
            'tenant': self.tenant,
            'expense_number': 'T-TEST-001',
            'status': 'paid',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('100.00'),
            'tax_amount': Decimal('0.00'),
            'expense_date': date(2026, 5, 22),
            'description': 'Test expense',
            'created_by': self.user,
        }
        defaults.update(kwargs)
        return Expense.all_objects.create(**defaults)

    def _create_je(self, *, description: str, entry_date: date, status: str = 'posted') -> JournalEntry:
        return JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number=f'JE-{JournalEntry.all_objects.count() + 1}',
            entry_date=entry_date,
            description=description,
            status=status,
            created_by=self.user,
        )

    def _link_je_to_expense(self, je: JournalEntry, expense: Expense) -> None:
        ct = ContentType.objects.get_for_model(Expense)
        je.source_content_type = ct
        je.source_object_id = expense.pk
        je.save(update_fields=['source_content_type', 'source_object_id'])

    def _add_line(self, je: JournalEntry, account_code: str, *, debit=Decimal('0'), credit=Decimal('0')) -> None:
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=self._account(account_code),
            debit_amount=debit,
            credit_amount=credit,
        )

    def test_p8_only_when_no_other_patterns(self):
        expense = self._create_expense(status='approved')
        approved = self._create_je(
            description='[expense_approved] T-TEST-001 - 100.00 EUR',
            entry_date=date(2026, 5, 22),
        )
        self._link_je_to_expense(approved, expense)
        self._add_line(approved, '4120', debit=Decimal('100'))
        self._add_line(approved, '2201', credit=Decimal('100'))
        SubledgerItem.all_objects.create(
            tenant=self.tenant,
            partner=self.supplier,
            direction='payable',
            source_content_type=ContentType.objects.get_for_model(Expense),
            source_object_id=expense.pk,
            journal_entry=approved,
            original_amount=Decimal('100.00'),
            open_amount=Decimal('100.00'),
            due_date=date(2026, 5, 22),
            status='open',
        )
        ctx = collect_expense_posting_context(self.tenant, expense)
        patterns = classify_document_mismatch(ctx)
        self.assertEqual(patterns, [P8_STANDARD_PATH_OK])

    def test_multiple_patterns_not_exclusive(self):
        expense = self._create_expense(expense_number='T-MULTI', amount=Decimal('8000.00'))
        cancelled_obligation = self._create_je(
            description='[expense_approved] T-MULTI - 8000.00 EUR',
            entry_date=date(2026, 5, 22),
            status='reversed',
        )
        self._link_je_to_expense(cancelled_obligation, expense)
        SubledgerItem.all_objects.create(
            tenant=self.tenant,
            partner=self.supplier,
            direction='payable',
            source_content_type=ContentType.objects.get_for_model(Expense),
            source_object_id=expense.pk,
            journal_entry=cancelled_obligation,
            original_amount=Decimal('8000.00'),
            open_amount=Decimal('0.00'),
            due_date=date(2026, 5, 22),
            status='cancelled',
        )
        manual = self._create_je(
            description='Nabava vozila — ručno knjiženje',
            entry_date=date(2026, 5, 22),
        )
        self._link_je_to_expense(manual, expense)
        self._add_line(manual, '0373', debit=Decimal('8000'))
        self._add_line(manual, '1000', credit=Decimal('8000'))
        SubledgerItem.all_objects.create(
            tenant=self.tenant,
            partner=self.other_partner,
            direction='payable',
            source_content_type=ContentType.objects.get_for_model(Expense),
            source_object_id=expense.pk,
            journal_entry=cancelled_obligation,
            original_amount=Decimal('8.61'),
            open_amount=Decimal('8.61'),
            due_date=date(2026, 5, 22),
            status='open',
        )
        ctx = collect_expense_posting_context(self.tenant, expense)
        patterns = classify_document_mismatch(ctx)
        self.assertIn(P1_CANCELLED_SUBLEDGER_PAID_DOC, patterns)
        self.assertIn(P2_MANUAL_JE_NO_SUBLEDGER, patterns)
        self.assertIn(P3_GL_BYPASS_AP_CANDIDATE, patterns)
        self.assertIn(P5_ORPHAN_SUBLEDGER_WRONG_PARTNER, patterns)
        self.assertNotIn(P8_STANDARD_PATH_OK, patterns)

    def test_p6_bank_without_canonical_allocation(self):
        from banking.models import BankStatement, BankTransaction
        from payments.models import BankAccount

        expense = self._create_expense(expense_number='T-BANK', status='paid', amount=Decimal('500.00'))
        manual = self._create_je(description='Bank payment manual', entry_date=date(2026, 6, 1))
        self._link_je_to_expense(manual, expense)
        bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni',
            bank_name='OTP',
            account_number='1',
            iban='HR6124070001100204771',
            currency='EUR',
        )
        statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='ST-1',
            bank_account=bank_account,
            statement_date=date(2026, 6, 1),
            opening_balance=Decimal('0'),
            closing_balance=Decimal('0'),
            imported_by=self.user,
        )
        BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=statement,
            transaction_date=date(2026, 6, 1),
            amount=Decimal('500.00'),
            transaction_type='debit',
            match_status='matched',
            matched_journal_entry=manual,
        )
        ctx = collect_expense_posting_context(self.tenant, expense)
        patterns = classify_document_mismatch(ctx)
        self.assertIn(P6_BANK_SETTLED_NO_ALLOCATION, patterns)

    def test_audit_tenant_returns_summary(self):
        expense = self._create_expense(status='approved', expense_number='T-AUDIT')
        approved = self._create_je(
            description='[expense_approved] T-AUDIT - 100.00 EUR',
            entry_date=date(2026, 5, 22),
        )
        self._link_je_to_expense(approved, expense)
        self._add_line(approved, '4120', debit=Decimal('100'))
        self._add_line(approved, '2201', credit=Decimal('100'))
        SubledgerItem.all_objects.create(
            tenant=self.tenant,
            partner=self.supplier,
            direction='payable',
            source_content_type=ContentType.objects.get_for_model(Expense),
            source_object_id=expense.pk,
            journal_entry=approved,
            original_amount=Decimal('100.00'),
            open_amount=Decimal('100.00'),
            due_date=date(2026, 5, 22),
            status='open',
        )
        report = audit_tenant_document_mismatches(self.tenant)
        self.assertEqual(report['tenant'], 'docint')
        self.assertGreaterEqual(report['summary']['documents_scanned'], 1)
        record = build_document_audit_record(collect_expense_posting_context(self.tenant, expense))
        self.assertIn(P8_STANDARD_PATH_OK, record['patterns'])
