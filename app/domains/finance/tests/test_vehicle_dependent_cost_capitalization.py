"""plan / execute / verify for vehicle dependent-cost capitalization."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounting.models import (
    AssetJournalLinkRole,
    ChartOfAccounts,
    DepreciationMethod,
    FixedAsset,
    FixedAssetJournalLink,
    FixedAssetOrigin,
    FixedAssetStatus,
    JournalEntry,
    SubledgerAllocation,
    SubledgerItem,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules
from accounting.services.rrif_import import import_rrif_chart
from accounts.models import AuditLog
from domains.assets.read.service import list_asset_journal_entries
from domains.finance.services.vehicle_dependent_cost_capitalization import (
    execute_vehicle_dependent_cost_capitalization,
    plan_vehicle_dependent_cost_capitalization,
    verify_vehicle_dependent_cost_capitalization,
)
from expenses.models import Expense, ExpenseCategory, ExpensePostingProfile, SettlementMethod
from partners.models import Partner
from tenants.management.commands.provision_finestar import apply_finestar_vehicle_expense_categories
from tenants.models import Tenant, TenantMembership


class VehicleDependentCostCapitalizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='vdep', name='Vehicle Dep Co')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_superuser(username='vdep-admin', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='owner')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='CVH Test',
            partner_type='supplier',
            status='active',
        )
        cls.account_4120 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4120')
        cls.account_0373 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='0373')
        cls.opex_category = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Ostalo',
            default_account=cls.account_4120,
        )
        apply_finestar_vehicle_expense_categories(cls.tenant)
        cls.purchase = cls._manual_purchase(cls.tenant, cls.user, cls.account_0373)
        cls.asset = FixedAsset.all_objects.create(
            tenant=cls.tenant,
            name='VW Golf',
            inventory_number='OS-3',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('17049.79'),
            purchase_date=date(2026, 5, 27),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=cls.account_0373,
            asset_account=ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='032001'),
            accumulated_depreciation_account=ChartOfAccounts.all_objects.get(
                tenant=cls.tenant, account_code='0393'
            ),
            depreciation_expense_account=ChartOfAccounts.all_objects.get(
                tenant=cls.tenant, account_code='4314'
            ),
            purchase_journal_entry=cls.purchase,
        )

    @staticmethod
    def _manual_purchase(tenant, user, account_0373):
        from accounting.models import JournalEntryLine

        entry = JournalEntry.all_objects.create(
            tenant=tenant,
            entry_number='202605-0012',
            entry_date=date(2026, 5, 27),
            description='purchase',
            status='posted',
            created_by=user,
        )
        cash = ChartOfAccounts.all_objects.get(tenant=tenant, account_code='1000')
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=account_0373,
            debit_amount=Decimal('17049.79'),
            credit_amount=Decimal('0.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=cash,
            debit_amount=Decimal('0.00'),
            credit_amount=Decimal('17049.79'),
        )
        return entry

    def _paid_opex(self, number, amount, tax) -> Expense:
        return Expense.all_objects.create(
            tenant=self.tenant,
            expense_number=number,
            status='paid',
            category=self.opex_category,
            supplier=self.supplier,
            amount=amount,
            tax_amount=tax,
            expense_date=date(2026, 7, 6),
            description=number,
            created_by=self.user,
            settlement_method=SettlementMethod.BUSINESS_ACCOUNT,
            posting_profile=ExpensePostingProfile.OPEX,
        )

    def test_plan_is_read_only_and_ready(self):
        expense = self._paid_opex('T-2026-0013', Decimal('82.95'), Decimal('16.59'))
        je_count = JournalEntry.all_objects.filter(tenant=self.tenant).count()
        plan = plan_vehicle_dependent_cost_capitalization(
            self.tenant,
            asset_id=self.asset.pk,
            expense_ids=[expense.pk],
            case_id='CASE-P',
        )
        self.assertTrue(plan.prerequisites_ok)
        self.assertTrue(plan.dry_run)
        self.assertEqual(JournalEntry.all_objects.filter(tenant=self.tenant).count(), je_count)
        expense.refresh_from_db()
        self.assertEqual(expense.posting_profile, ExpensePostingProfile.OPEX)
        self.assertIn('reclassify_posting_profile', [step['action'] for step in plan.steps])

    def test_execute_rejects_empty_snapshot(self):
        expense = self._paid_opex('T-2026-0014', Decimal('50.05'), Decimal('10.01'))
        with self.assertRaises(ValidationError):
            execute_vehicle_dependent_cost_capitalization(
                self.tenant,
                asset_id=self.asset.pk,
                expense_ids=[expense.pk],
                snapshot={},
                user=self.user,
                reason='x',
                case_id='CASE-E',
            )

    def test_execute_capitalizes_and_reconciles_from_gl(self):
        first = self._paid_opex('T-2026-0013', Decimal('82.95'), Decimal('16.59'))
        second = self._paid_opex('T-2026-0014', Decimal('50.05'), Decimal('10.01'))
        third = self._paid_opex('T-2026-0015', Decimal('231.36'), Decimal('7.49'))
        ids = [first.pk, second.pk, third.pk]
        plan = plan_vehicle_dependent_cost_capitalization(
            self.tenant,
            asset_id=self.asset.pk,
            expense_ids=ids,
            case_id='GOLF-CVH-CAP',
        )
        self.assertTrue(plan.prerequisites_ok, plan.blockers)
        result = execute_vehicle_dependent_cost_capitalization(
            self.tenant,
            asset_id=self.asset.pk,
            expense_ids=ids,
            snapshot=plan.snapshot(),
            user=self.user,
            reason='Kapitalizacija CVH registracije',
            case_id='GOLF-CVH-CAP',
        )
        self.assertTrue(result['verify']['ok'])
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.acquisition_cost, Decimal('17380.06'))
        recon = list_asset_journal_entries(self.tenant, self.asset.pk)['reconciliation']
        self.assertEqual(recon['capitalized_net'], '17380.06')
        self.assertTrue(recon['balanced'])

        for expense in (first, second, third):
            expense.refresh_from_db()
            self.assertEqual(expense.posting_profile, ExpensePostingProfile.ASSET_PURCHASE)
            live = SubledgerItem.all_objects.filter(
                tenant=self.tenant,
                source_object_id=expense.pk,
            ).exclude(status='cancelled')
            self.assertEqual(live.count(), 1)
            self.assertEqual(live.first().status, 'closed')
            cancelled = SubledgerItem.all_objects.filter(
                tenant=self.tenant,
                source_object_id=expense.pk,
                status='cancelled',
            )
            for item in cancelled:
                self.assertFalse(SubledgerAllocation.all_objects.filter(subledger_item=item).exists())

        self.assertEqual(
            FixedAssetJournalLink.all_objects.filter(
                tenant=self.tenant,
                fixed_asset=self.asset,
                role=AssetJournalLinkRole.DEPENDENT_COST,
            ).count(),
            3,
        )
        self.assertTrue(
            AuditLog.all_objects.filter(
                action='expense_posting_profile_reclassified',
                changes__case_id='GOLF-CVH-CAP',
            ).exists()
        )
        self.assertTrue(
            AuditLog.all_objects.filter(
                action='fixed_asset_acquisition_cost_reconciled',
                changes__case_id='GOLF-CVH-CAP',
            ).exists()
        )

        again = execute_vehicle_dependent_cost_capitalization(
            self.tenant,
            asset_id=self.asset.pk,
            expense_ids=ids,
            snapshot=plan_vehicle_dependent_cost_capitalization(
                self.tenant,
                asset_id=self.asset.pk,
                expense_ids=ids,
                case_id='GOLF-CVH-CAP',
            ).snapshot(),
            user=self.user,
            reason='idempotent',
            case_id='GOLF-CVH-CAP',
        )
        self.assertTrue(all(row['skipped'] for row in again['applied']))
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.acquisition_cost, Decimal('17380.06'))

    def test_vehicle_category_does_not_imply_asset_purchase(self):
        category = ExpenseCategory.all_objects.get(tenant=self.tenant, code='vehicle_registration')
        self.assertIsNone(category.default_account_id)
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-VEH-REG',
            status='paid',
            category=category,
            supplier=self.supplier,
            amount=Decimal('82.95'),
            tax_amount=Decimal('16.59'),
            expense_date=date(2026, 7, 6),
            description='reg',
            created_by=self.user,
            settlement_method=SettlementMethod.BUSINESS_ACCOUNT,
            posting_profile=ExpensePostingProfile.OPEX,
        )
        entry = JournalEntry.all_objects.get(
            tenant=self.tenant,
            source_object_id=expense.pk,
            description__startswith='[expense_approved]',
            status='posted',
        )
        debits = {
            line.account.account_code.split('-', 1)[0]
            for line in entry.lines.select_related('account')
            if line.debit_amount > 0
        }
        self.assertIn('4120', debits)
        self.assertNotIn('0373', debits)
        self.assertEqual(expense.posting_profile, ExpensePostingProfile.OPEX)

    def test_command_plan_fails_closed_without_writes(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command(
                'plan_vehicle_dependent_cost_capitalization',
                tenant='vdep',
                asset_id=self.asset.pk,
                expense_id=[999999],
                stdout=out,
            )
        self.assertEqual(self.asset.acquisition_cost, Decimal('17049.79'))
