"""ensure_default_posting_rules is create-only and does not overwrite tenant rules."""

from __future__ import annotations

from django.test import TestCase

from accounting.models import PostingRule
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules
from accounting.services.rrif_import import import_rrif_chart
from expenses.models import ExpensePostingProfile
from tenants.models import Tenant


class EnsureDefaultPostingRulesIdempotencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='posting-seed', name='Posting Seed')
        provision_tenant_chart(cls.tenant)

    def test_create_only_does_not_overwrite_existing_tenant_rule(self):
        PostingRule.all_objects.filter(tenant=self.tenant).delete()
        existing = PostingRule.all_objects.create(
            tenant=self.tenant,
            name='Odobren trošak — rashod / dobavljač',
            document_type='expense_approved',
            debit_account_code='4999',
            credit_account_code='2201',
            amount_field='net_amount',
            priority=10,
            use_analytic=True,
            condition={'tenant_custom': True},
            is_active=True,
        )

        created_first = ensure_default_posting_rules(self.tenant)
        self.assertGreaterEqual(created_first, 1)

        existing.refresh_from_db()
        self.assertEqual(existing.debit_account_code, '4999')
        self.assertEqual(existing.condition, {'tenant_custom': True})

        created_second = ensure_default_posting_rules(self.tenant)
        self.assertEqual(created_second, 0)

        asset_rules = list(
            PostingRule.all_objects.filter(
                tenant=self.tenant,
                document_type='expense_approved',
                debit_account_code='0373',
                credit_account_code='2201',
                is_active=True,
            )
        )
        self.assertEqual(len(asset_rules), 1)
        self.assertEqual(
            asset_rules[0].condition.get('posting_profile'),
            [ExpensePostingProfile.ASSET_PURCHASE],
        )
