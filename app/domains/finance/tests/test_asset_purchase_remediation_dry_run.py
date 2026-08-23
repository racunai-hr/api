"""Fail-closed dry-run tests for T-2026-0009 remediation planner (no DB writes on finestar)."""

from __future__ import annotations

from django.test import TestCase

from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules
from accounting.services.rrif_import import import_rrif_chart
from accounting.models import PostingRule
from domains.finance.services.expense_asset_purchase_remediation import (
    _has_asset_purchase_obligation_rule,
    plan_t20260009_asset_purchase_remediation,
)
from expenses.models import ExpensePostingProfile
from tenants.models import Tenant


class AssetPurchaseRemediationDryRunTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='remediation-plan', name='Remediation Plan')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)

    def test_missing_expense_is_fail_closed(self):
        plan = plan_t20260009_asset_purchase_remediation(self.tenant, expense_id=16)
        self.assertFalse(plan.prerequisites_ok)
        self.assertFalse(plan.writes_allowed)
        self.assertTrue(plan.dry_run)
        self.assertTrue(any('not found' in b.lower() or 'expense' in b.lower() for b in plan.blockers))
        self.assertEqual(plan.steps[0]['action'], 'blocked')

    def test_asset_purchase_obligation_rule_seeded_by_ensure_default(self):
        self.assertTrue(_has_asset_purchase_obligation_rule(self.tenant))
        rule = PostingRule.all_objects.get(
            tenant=self.tenant,
            document_type='expense_approved',
            debit_account_code='0373',
            credit_account_code='2201',
            is_active=True,
        )
        self.assertIn(
            ExpensePostingProfile.ASSET_PURCHASE,
            rule.condition.get('posting_profile', []),
        )
