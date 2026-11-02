"""reconcile_acquisition_cost derives the target from capitalized GL, not an operator amount."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from inspect import signature

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import (
    AccountType,
    AssetJournalLinkRole,
    ChartOfAccounts,
    DepreciationMethod,
    DepreciationSchedule,
    FixedAsset,
    FixedAssetJournalLink,
    FixedAssetOrigin,
    FixedAssetStatus,
    JournalEntry,
    JournalEntryLine,
)
from accounts.models import AuditLog
from domains.assets.services.acquisition_cost import reconcile_acquisition_cost
from tenants.models import Tenant


class ReconcileAcquisitionCostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.tenant = Tenant.objects.create(slug='acqrec', name='Acq Rec Co')
        cls.user = User.objects.create_superuser(username='acq-admin', password='test')
        account_type = AccountType.all_objects.create(tenant=cls.tenant, name='asset')
        cls.accounts = {
            code: ChartOfAccounts.all_objects.create(
                tenant=cls.tenant,
                account_code=code,
                account_name=code,
                account_type=account_type,
                is_postable=True,
            )
            for code in ('0373', '032001', '0393', '4314', '1000')
        }
        cls.purchase = cls._je('P-1', Decimal('1000.00'))
        cls.dependent = cls._je('D-1', Decimal('330.27'))
        cls.asset = FixedAsset.all_objects.create(
            tenant=cls.tenant,
            name='Golf',
            inventory_number='OS-3',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('1000.00'),
            purchase_date=date(2026, 5, 27),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=cls.accounts['0373'],
            asset_account=cls.accounts['032001'],
            accumulated_depreciation_account=cls.accounts['0393'],
            depreciation_expense_account=cls.accounts['4314'],
            purchase_journal_entry=cls.purchase,
        )

    @classmethod
    def _je(cls, number, debit):
        entry = JournalEntry.all_objects.create(
            tenant=cls.tenant,
            entry_number=number,
            entry_date=date(2026, 7, 6),
            description=number,
            status='posted',
            created_by=cls.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=cls.accounts['0373'],
            debit_amount=debit,
            credit_amount=Decimal('0.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=cls.accounts['1000'],
            debit_amount=Decimal('0.00'),
            credit_amount=debit,
        )
        return entry

    def _link_dependent(self):
        FixedAssetJournalLink.all_objects.create(
            tenant=self.tenant,
            fixed_asset=self.asset,
            journal_entry=self.dependent,
            role=AssetJournalLinkRole.DEPENDENT_COST,
        )

    def test_signature_has_no_to_amount(self):
        self.assertNotIn('to_amount', signature(reconcile_acquisition_cost).parameters)

    def test_derives_target_from_capitalized_net(self):
        self._link_dependent()
        updated = reconcile_acquisition_cost(
            self.asset,
            expected_from_amount=Decimal('1000.00'),
            user=self.user,
            reason='test reconcile',
            case_id='CASE-1',
        )
        self.assertEqual(updated.acquisition_cost, Decimal('1330.27'))
        log = AuditLog.all_objects.get(action='fixed_asset_acquisition_cost_reconciled')
        self.assertEqual(log.changes['from_amount'], '1000.00')
        self.assertEqual(log.changes['to_amount'], '1330.27')
        self.assertEqual(log.changes['case_id'], 'CASE-1')

    def test_idempotent_when_already_balanced(self):
        self._link_dependent()
        reconcile_acquisition_cost(
            self.asset,
            expected_from_amount=Decimal('1000.00'),
            user=self.user,
            reason='first',
            case_id='CASE-2',
        )
        self.asset.refresh_from_db()
        again = reconcile_acquisition_cost(
            self.asset,
            expected_from_amount=Decimal('1330.27'),
            user=self.user,
            reason='second',
            case_id='CASE-2b',
        )
        self.assertEqual(again.acquisition_cost, Decimal('1330.27'))
        self.assertEqual(
            AuditLog.all_objects.filter(action='fixed_asset_acquisition_cost_reconciled').count(),
            1,
        )

    def test_rejects_wrong_expected_from_amount(self):
        with self.assertRaises(ValidationError):
            reconcile_acquisition_cost(
                self.asset,
                expected_from_amount=Decimal('999.00'),
                user=self.user,
                reason='bad lock',
                case_id='CASE-3',
            )

    def test_rejects_active_asset(self):
        self.asset.status = FixedAssetStatus.ACTIVE
        self.asset.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            reconcile_acquisition_cost(
                self.asset,
                expected_from_amount=Decimal('1000.00'),
                user=self.user,
                reason='active',
                case_id='CASE-4',
            )

    def test_rejects_empty_reason_or_case(self):
        with self.assertRaises(ValidationError):
            reconcile_acquisition_cost(
                self.asset,
                expected_from_amount=Decimal('1000.00'),
                user=self.user,
                reason='',
                case_id='CASE-5',
            )
        with self.assertRaises(ValidationError):
            reconcile_acquisition_cost(
                self.asset,
                expected_from_amount=Decimal('1000.00'),
                user=self.user,
                reason='x',
                case_id='',
            )

    def test_rejects_decrease_against_card(self):
        self.asset.acquisition_cost = Decimal('2000.00')
        self.asset.save(update_fields=['acquisition_cost'])
        with self.assertRaises(ValidationError) as ctx:
            reconcile_acquisition_cost(
                self.asset,
                expected_from_amount=Decimal('2000.00'),
                user=self.user,
                reason='decrease',
                case_id='CASE-6',
            )
        self.assertIn('capitalized_net', ctx.exception.message_dict)

    def test_rejects_posted_depreciation(self):
        dep_je = self._je('DEP-1', Decimal('10.00'))
        DepreciationSchedule.all_objects.create(
            tenant=self.tenant,
            fixed_asset=self.asset,
            year=2026,
            month=8,
            amount=Decimal('10.00'),
            journal_entry=dep_je,
        )
        with self.assertRaises(ValidationError):
            reconcile_acquisition_cost(
                self.asset,
                expected_from_amount=Decimal('1000.00'),
                user=self.user,
                reason='dep',
                case_id='CASE-7',
            )
