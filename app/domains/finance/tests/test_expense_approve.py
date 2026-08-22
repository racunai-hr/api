"""approve_expense_for_posting — settlement_method override (backward-compatible)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from accounting.models import JournalEntry
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules
from accounting.services.rrif_import import import_rrif_chart
from domains.finance.services.expenses import approve_expense_for_posting
from domains.finance.services.subledger import get_subledger_item_for_source
from expenses.models import Expense, ExpenseCategory, ExpenseSource, SettlementMethod
from partners.models import Partner
from tenants.models import Tenant, TenantMembership


class ApproveExpenseSettlementOverrideTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='approve-sm', name='Approve SM')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.owner = User.objects.create_user(username='approve-sm-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='CVH Test',
            partner_type='supplier',
            status='active',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def _draft(self, *, number: str, settlement_method: str = '') -> Expense:
        return Expense.all_objects.create(
            tenant=self.tenant,
            expense_number=number,
            source=ExpenseSource.MANUAL,
            status='draft',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('82.95'),
            tax_amount=Decimal('16.59'),
            currency='EUR',
            expense_date=date(2026, 7, 6),
            settlement_method=settlement_method,
            description='Approve settlement override test',
            created_by=self.owner,
        )

    def _approved_jes(self, expense: Expense):
        ct = ContentType.objects.get_for_model(Expense)
        return JournalEntry.all_objects.filter(
            tenant=self.tenant,
            source_content_type=ct,
            source_object_id=expense.pk,
            description__startswith='[expense_approved]',
            status='posted',
        )

    def _paid_jes(self, expense: Expense):
        ct = ContentType.objects.get_for_model(Expense)
        return JournalEntry.all_objects.filter(
            tenant=self.tenant,
            source_content_type=ct,
            source_object_id=expense.pk,
            description__startswith='[expense_paid]',
            status='posted',
        )

    def test_default_caller_fills_business_account(self):
        expense = self._draft(number='T-APPR-0001')
        dto = approve_expense_for_posting(
            tenant=self.tenant,
            expense_id=expense.pk,
            user=self.owner,
        )
        expense.refresh_from_db()
        self.assertEqual(dto['status'], 'approved')
        self.assertEqual(expense.status, 'approved')
        self.assertEqual(expense.settlement_method, SettlementMethod.BUSINESS_ACCOUNT)
        self.assertEqual(self._approved_jes(expense).count(), 1)
        self.assertEqual(self._paid_jes(expense).count(), 0)
        item = get_subledger_item_for_source(self.tenant, expense)
        self.assertIsNotNone(item)
        self.assertEqual(item.status, 'open')

    def test_explicit_blank_keeps_settlement_blank(self):
        expense = self._draft(number='T-APPR-0002')
        dto = approve_expense_for_posting(
            tenant=self.tenant,
            expense_id=expense.pk,
            user=self.owner,
            settlement_method='',
        )
        expense.refresh_from_db()
        self.assertEqual(dto['status'], 'approved')
        self.assertEqual(dto['settlement_method'], '')
        self.assertEqual(expense.status, 'approved')
        self.assertEqual(expense.settlement_method, '')
        self.assertEqual(self._approved_jes(expense).count(), 1)
        self.assertEqual(self._paid_jes(expense).count(), 0)
        item = get_subledger_item_for_source(self.tenant, expense)
        self.assertIsNotNone(item)
        self.assertEqual(item.status, 'open')
        self.assertEqual(item.open_amount, Decimal('82.95'))
