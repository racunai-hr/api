"""Remediation write-path gates: freeze holds, narrow migrate, dry-run no-write."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import PostingRule
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import (
    build_document_posting_plan,
    ensure_default_posting_rules,
    post_document,
)
from accounting.services.rrif_import import import_rrif_chart
from domains.finance.services.expense_asset_purchase_remediation import (
    CASE_ID,
    execute_t20260009_asset_purchase_remediation,
    migrate_expense_posting_profile_opex_to_asset_purchase,
    plan_t20260009_asset_purchase_remediation,
)
from expenses.models import Expense, ExpenseCategory, ExpensePostingProfile, SettlementMethod
from partners.models import Partner
from tenants.models import Tenant, TenantMembership


class RemediationProfileEscapeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='remediation-write', name='Remediation Write')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='remediation-write-user', password='x')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='owner')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Supplier',
            partner_type='supplier',
            status='active',
        )
        from accounting.models import ChartOfAccounts
        cls.account_4120 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4120')
        cls.category = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Ostalo',
            default_account=cls.account_4120,
        )

    def _paid_opex_expense(self, **overrides) -> Expense:
        defaults = {
            'tenant': self.tenant,
            'expense_number': 'T-REM-0001',
            'status': 'paid',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('100.00'),
            'tax_amount': Decimal('0.00'),
            'expense_date': date(2026, 5, 22),
            'description': 'test',
            'created_by': self.user,
            'settlement_method': SettlementMethod.BUSINESS_ACCOUNT,
            'posting_profile': ExpensePostingProfile.OPEX,
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def test_normal_save_still_freezes_posting_profile(self):
        expense = self._paid_opex_expense()
        expense.posting_profile = ExpensePostingProfile.ASSET_PURCHASE
        with self.assertRaises(ValidationError):
            expense.save(update_fields=['posting_profile'])
        expense.refresh_from_db()
        self.assertEqual(expense.posting_profile, ExpensePostingProfile.OPEX)

    def test_remediation_migrate_allows_opex_to_asset_purchase_with_audit(self):
        expense = self._paid_opex_expense(expense_number='T-REM-0002')
        migrate_expense_posting_profile_opex_to_asset_purchase(
            expense,
            user=self.user,
            reason='controlled remediation test path',
            case_id=CASE_ID,
        )
        expense.refresh_from_db()
        self.assertEqual(expense.posting_profile, ExpensePostingProfile.ASSET_PURCHASE)

    def test_legacy_unscoped_opex_rule_does_not_fire_for_asset_purchase(self):
        PostingRule.all_objects.filter(
            tenant=self.tenant,
            document_type='expense_approved',
            debit_account_code='4120',
        ).update(condition={})
        expense = self._paid_opex_expense(
            expense_number='T-REM-0003',
            posting_profile=ExpensePostingProfile.ASSET_PURCHASE,
            amount=Decimal('8000.00'),
        )
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        self.assertIsNotNone(plan)
        debit_bases = {
            line.debit_account.account_code.split('-', 1)[0]
            for line in plan.lines
        }
        self.assertEqual(debit_bases, {'0373'})
        self.assertNotIn('4120', debit_bases)

        entry = post_document(self.tenant, expense, 'expense_approved', self.user)
        self.assertIsNotNone(entry)
        codes = {
            line.account.account_code.split('-', 1)[0]
            for line in entry.lines.select_related('account')
            if line.debit_amount > 0
        }
        self.assertEqual(codes, {'0373'})


class RemediationDryRunNoWriteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='remediation-dry', name='Remediation Dry')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='remediation-dry-user', password='x')

    def test_missing_expense_fail_closed(self):
        plan = plan_t20260009_asset_purchase_remediation(self.tenant, expense_id=16)
        self.assertFalse(plan.prerequisites_ok)
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.writes_allowed)

    def test_execute_default_is_dry_run(self):
        result = execute_t20260009_asset_purchase_remediation(
            self.tenant,
            self.user,
            execute=False,
        )
        self.assertFalse(result.executed)
        self.assertTrue(result.dry_run)
        self.assertFalse(result.prerequisites_ok)
