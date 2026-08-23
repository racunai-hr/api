"""FixedAssetJournalLink validations — no REVERSAL role, no lifecycle FK overlap."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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
)
from tenants.models import Tenant


class FixedAssetJournalLinkModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.tenant = Tenant.objects.create(slug='linkmodel', name='Link Model Co')
        cls.other = Tenant.objects.create(slug='linkother', name='Link Other Co')
        cls.user = User.objects.create_user(username='link-user', password='test')
        cls.accounts = cls._accounts(cls.tenant)
        other_accounts = cls._accounts(cls.other)
        cls.purchase = cls._je(cls.tenant, cls.user, '202605-P', 'posted')
        cls.activation = cls._je(cls.tenant, cls.user, '202607-A', 'posted')
        cls.dep_je = cls._je(cls.tenant, cls.user, '202607-D', 'posted')
        cls.extra = cls._je(cls.tenant, cls.user, '202606-X', 'posted')
        cls.draft = cls._je(cls.tenant, cls.user, '202606-DR', 'draft')
        cls.other_je = cls._je(cls.other, cls.user, 'OTHER-1', 'posted')
        cls.asset = cls._asset(cls.tenant, cls.accounts, cls.purchase, cls.activation)
        cls.other_asset = cls._asset(
            cls.other,
            other_accounts,
            cls._je(cls.other, cls.user, 'OTHER-P', 'posted'),
            None,
        )
        DepreciationSchedule.all_objects.create(
            tenant=cls.tenant,
            fixed_asset=cls.asset,
            year=2026,
            month=7,
            amount=Decimal('10.00'),
            journal_entry=cls.dep_je,
        )

    @staticmethod
    def _accounts(tenant):
        account_type = AccountType.all_objects.create(tenant=tenant, name='asset')
        return {
            code: ChartOfAccounts.all_objects.create(
                tenant=tenant,
                account_code=code,
                account_name=code,
                account_type=account_type,
                is_postable=True,
            )
            for code in ('0373', '032001', '0393', '4314')
        }

    @staticmethod
    def _je(tenant, user, number, status):
        return JournalEntry.all_objects.create(
            tenant=tenant,
            entry_number=number,
            entry_date=date(2026, 6, 1),
            description=number,
            status=status,
            created_by=user,
        )

    @staticmethod
    def _asset(tenant, accounts, purchase, activation):
        return FixedAsset.all_objects.create(
            tenant=tenant,
            name='Vozilo',
            inventory_number='OS-1',
            vin='WVWZZZCD8PW153457',
            status=FixedAssetStatus.IN_PREPARATION if activation is None else FixedAssetStatus.ACTIVE,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('100.00'),
            purchase_date=date(2026, 5, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=accounts['0373'],
            asset_account=accounts['032001'],
            accumulated_depreciation_account=accounts['0393'],
            depreciation_expense_account=accounts['4314'],
            purchase_journal_entry=purchase,
            activation_journal_entry=activation,
        )

    def _link(self, **overrides):
        data = {
            'tenant': self.tenant,
            'fixed_asset': self.asset,
            'journal_entry': self.extra,
            'role': AssetJournalLinkRole.DEPENDENT_COST,
        }
        data.update(overrides)
        return FixedAssetJournalLink(**data)

    def test_valid_dependent_cost_saves(self):
        link = self._link()
        link.full_clean()
        link.save()
        self.assertEqual(FixedAssetJournalLink.all_objects.count(), 1)

    def test_reversal_is_not_a_valid_role(self):
        link = self._link(role='reversal')
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_draft_journal_entry_rejected(self):
        link = self._link(journal_entry=self.draft)
        with self.assertRaises(ValidationError) as ctx:
            link.full_clean()
        self.assertIn('journal_entry', ctx.exception.message_dict)

    def test_purchase_journal_entry_rejected(self):
        link = self._link(journal_entry=self.purchase)
        with self.assertRaises(ValidationError) as ctx:
            link.full_clean()
        self.assertIn('nabave', str(ctx.exception))

    def test_activation_journal_entry_rejected(self):
        link = self._link(journal_entry=self.activation)
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_depreciation_schedule_journal_entry_rejected(self):
        link = self._link(journal_entry=self.dep_je)
        with self.assertRaises(ValidationError) as ctx:
            link.full_clean()
        self.assertIn('amortizacije', str(ctx.exception))

    def test_cross_tenant_journal_entry_rejected(self):
        link = self._link(journal_entry=self.other_je)
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_unique_pair(self):
        first = self._link()
        first.full_clean()
        first.save()
        duplicate = self._link()
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FixedAssetJournalLink.all_objects.create(
                    tenant=self.tenant,
                    fixed_asset=self.asset,
                    journal_entry=self.extra,
                    role=AssetJournalLinkRole.PAYMENT,
                )
