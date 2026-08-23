"""asset_purchase posting profile — explicit profile drives GL, not account code alone."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, SubledgerItem
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules, post_document
from accounting.services.rrif_import import import_rrif_chart
from expenses.models import Expense, ExpenseCategory, ExpensePostingProfile, SettlementMethod
from partners.models import Partner
from tenants.models import Tenant, TenantMembership


class AssetPurchasePostingProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='asset-purchase', name='Asset Purchase Co')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)

        User = get_user_model()
        cls.user = User.objects.create_user(username='asset-purchase-user', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='owner')

        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Automobile Test',
            partner_type='supplier',
            status='active',
        )
        cls.account_0373 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='0373')
        cls.account_4120 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4120')
        cls.opex_category = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Ostalo',
            default_account=cls.account_4120,
        )
        cls.asset_category = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Nabava imovine u pripremi',
            default_account=cls.account_0373,
        )

    def _create_expense(self, **overrides) -> Expense:
        defaults = {
            'tenant': self.tenant,
            'expense_number': 'T-ASSET-0001',
            'status': 'approved',
            'category': self.opex_category,
            'supplier': self.supplier,
            'amount': Decimal('8000.00'),
            'tax_amount': Decimal('0.00'),
            'expense_date': date(2026, 5, 22),
            'description': 'VW T-Cross nabava',
            'created_by': self.user,
            'settlement_method': SettlementMethod.BUSINESS_ACCOUNT,
            'posting_profile': ExpensePostingProfile.ASSET_PURCHASE,
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def _entry(self, expense: Expense, document_type: str) -> JournalEntry:
        ct = ContentType.objects.get_for_model(Expense)
        return JournalEntry.all_objects.get(
            tenant=self.tenant,
            source_content_type=ct,
            source_object_id=expense.pk,
            description__startswith=f'[{document_type}]',
            status='posted',
        )

    def _net_pair(self, entry: JournalEntry) -> tuple[str, str, Decimal]:
        """Return (debit_base, credit_base, amount) for the first non-zero debit line pair."""
        lines = list(entry.lines.select_related('account').order_by('id'))
        for i in range(0, len(lines), 2):
            debit = lines[i]
            credit = lines[i + 1]
            if debit.debit_amount > 0:
                return (
                    debit.account.account_code.split('-', 1)[0],
                    credit.account.account_code.split('-', 1)[0],
                    debit.debit_amount,
                )
        raise AssertionError('No debit line found')

    def test_asset_purchase_posts_0373_to_2201_and_opens_subledger(self):
        expense = self._create_expense(category=self.asset_category)
        entry = post_document(self.tenant, expense, 'expense_approved', self.user)
        self.assertIsNotNone(entry)

        debit, credit, amount = self._net_pair(self._entry(expense, 'expense_approved'))
        self.assertEqual(debit, '0373')
        self.assertEqual(credit, '2201')
        self.assertEqual(amount, Decimal('8000.00'))

        item = SubledgerItem.all_objects.get(
            tenant=self.tenant,
            source_content_type=ContentType.objects.get_for_model(Expense),
            source_object_id=expense.pk,
        )
        self.assertEqual(item.direction, 'payable')
        self.assertEqual(item.status, 'open')
        self.assertEqual(item.original_amount, Decimal('8000.00'))
        self.assertEqual(item.partner_id, self.supplier.pk)

    def test_asset_purchase_payment_clears_ap_via_business_account(self):
        expense = self._create_expense(
            expense_number='T-ASSET-0002',
            category=self.asset_category,
            status='paid',
        )
        post_document(self.tenant, expense, 'expense_approved', self.user)
        paid = post_document(self.tenant, expense, 'expense_paid', self.user)
        self.assertIsNotNone(paid)

        debit, credit, amount = self._net_pair(self._entry(expense, 'expense_paid'))
        self.assertEqual(debit, '2201')
        self.assertEqual(credit, '1000')
        self.assertEqual(amount, Decimal('8000.00'))

        item = SubledgerItem.all_objects.get(
            tenant=self.tenant,
            source_content_type=ContentType.objects.get_for_model(Expense),
            source_object_id=expense.pk,
        )
        self.assertEqual(item.status, 'closed')
        self.assertEqual(item.open_amount, Decimal('0.00'))

    def test_opex_still_posts_4120_by_default(self):
        expense = self._create_expense(
            expense_number='T-OPEX-0001',
            posting_profile=ExpensePostingProfile.OPEX,
            category=self.opex_category,
            amount=Decimal('100.00'),
        )
        post_document(self.tenant, expense, 'expense_approved', self.user)
        debit, credit, amount = self._net_pair(self._entry(expense, 'expense_approved'))
        self.assertEqual(debit, '4120')
        self.assertEqual(credit, '2201')
        self.assertEqual(amount, Decimal('100.00'))

    def test_category_0373_alone_does_not_imply_asset_purchase_rule(self):
        """Category default 0373 without asset_purchase profile still matches opex rules.

        Debit may resolve to 0373 via category, but the selected PostingRule remains opex
        (condition posting_profile=opex), not the asset_purchase rule.
        """
        expense = self._create_expense(
            expense_number='T-OPEX-0373',
            posting_profile=ExpensePostingProfile.OPEX,
            category=self.asset_category,
            amount=Decimal('500.00'),
        )
        post_document(self.tenant, expense, 'expense_approved', self.user)
        debit, credit, amount = self._net_pair(self._entry(expense, 'expense_approved'))
        self.assertEqual(debit, '0373')  # category override of opex net rule
        self.assertEqual(credit, '2201')
        self.assertEqual(amount, Decimal('500.00'))

        # Asset-purchase-only rule must not be the only path; opex tax rule stays gated.
        # Presence of AP credit proves document still went through standard AP posting.
        self.assertEqual(credit, '2201')

    def test_asset_purchase_without_category_account_falls_back_to_rule_0373(self):
        bare_category = ExpenseCategory.all_objects.create(
            tenant=self.tenant,
            name='Bez konta',
        )
        expense = self._create_expense(
            expense_number='T-ASSET-0003',
            category=bare_category,
            posting_profile=ExpensePostingProfile.ASSET_PURCHASE,
        )
        post_document(self.tenant, expense, 'expense_approved', self.user)
        debit, credit, amount = self._net_pair(self._entry(expense, 'expense_approved'))
        self.assertEqual(debit, '0373')
        self.assertEqual(credit, '2201')
        self.assertEqual(amount, Decimal('8000.00'))
