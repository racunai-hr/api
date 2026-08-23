"""Asset-scoped journal-link backfill — plan is read-only, execute uses snapshot."""

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
    AccountType,
    AssetJournalLinkRole,
    ChartOfAccounts,
    DepreciationMethod,
    FixedAsset,
    FixedAssetJournalLink,
    FixedAssetOrigin,
    FixedAssetStatus,
    JournalEntry,
    JournalEntryLine,
)
from accounts.models import AuditLog
from domains.assets.services.journal_links import (
    apply_asset_journal_links,
    plan_asset_journal_links,
    verify_asset_journal_links,
)
from tenants.models import Tenant


class JournalLinkBackfillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.tenant = Tenant.objects.create(slug='linkbf', name='Link BF Co')
        cls.other = Tenant.objects.create(slug='linkbfother', name='Other BF')
        cls.user = User.objects.create_superuser(username='bf-admin', password='test')
        cls.accounts = cls._accounts(cls.tenant)
        other_accounts = cls._accounts(cls.other)
        vin = 'WVWZZZCD8PW153457'
        cls.purchase = cls._je(
            cls.tenant,
            cls.user,
            '202605-0012',
            '70025249|WVWZZZCD8PW153457|PRIVATE',
            Decimal('15882.35'),
            cls.accounts,
        )
        cls.ppmv_obveza = cls._je(
            cls.tenant,
            cls.user,
            '202606-0011',
            'PPMV-OBVEZA|WVWZZZCD8PW153457|513',
            Decimal('1051.04'),
            cls.accounts,
        )
        cls.ppmv_original = cls._je(
            cls.tenant,
            cls.user,
            '202606-0010',
            'PPMV|WVWZZZCD8PW153457|ZOU',
            Decimal('1051.04'),
            cls.accounts,
            status='reversed',
        )
        cls.ppmv_storno = cls._je(
            cls.tenant,
            cls.user,
            '202606-0010-ST',
            'PPMV|WVWZZZCD8PW153457|ZOU',
            Decimal('0'),
            cls.accounts,
            credit_construction=Decimal('1051.04'),
            reversed_entry=cls.ppmv_original,
        )
        cls.ppmv_uplata = cls._je(
            cls.tenant,
            cls.user,
            '202606-0012',
            'PPMV-UPLATA|WVWZZZCD8PW153457|ZOU',
            Decimal('0'),
            cls.accounts,
            credit_construction=Decimal('0'),
            extra_debit_account='2201',
            extra_debit=Decimal('1051.04'),
        )
        cls.fzoeu_vozila = cls._je(
            cls.tenant,
            cls.user,
            '202607-0034',
            'FZOEU-VOZILA|WVWZZZCD8PW153457|PNB',
            Decimal('112.80'),
            cls.accounts,
        )
        cls.fzoeu_gume = cls._je(
            cls.tenant,
            cls.user,
            '202607-0035',
            'FZOEU-GUME|WVWZZZCD8PW153457|PNB',
            Decimal('3.60'),
            cls.accounts,
        )
        cls.unrelated = cls._je(
            cls.tenant,
            cls.user,
            '202607-9999',
            'FZOEU-VOZILA|WVGZZZC1ZPY022544|OTHER',
            Decimal('10.00'),
            cls.accounts,
        )
        cls.asset = FixedAsset.all_objects.create(
            tenant=cls.tenant,
            name='VW Golf',
            vin=vin,
            inventory_number='OS-3',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('17049.79'),
            purchase_date=date(2026, 5, 27),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=cls.accounts['0373'],
            asset_account=cls.accounts['032001'],
            accumulated_depreciation_account=cls.accounts['0393'],
            depreciation_expense_account=cls.accounts['4314'],
            purchase_journal_entry=cls.purchase,
        )
        cls.other_asset = FixedAsset.all_objects.create(
            tenant=cls.other,
            name='Other',
            vin='WVGZZZC1ZPY022544',
            inventory_number='XX',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('10.00'),
            purchase_date=date(2026, 1, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=other_accounts['0373'],
            asset_account=other_accounts['032001'],
            accumulated_depreciation_account=other_accounts['0393'],
            depreciation_expense_account=other_accounts['4314'],
            purchase_journal_entry=cls._je(
                cls.other,
                cls.user,
                'OTHER-P',
                'OTHER',
                Decimal('10.00'),
                other_accounts,
            ),
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
            for code in ('0373', '032001', '0393', '4314', '1000', '2201')
        }

    @staticmethod
    def _je(
        tenant,
        user,
        number,
        reference,
        construction_debit,
        accounts,
        *,
        status='posted',
        credit_construction=None,
        reversed_entry=None,
        extra_debit_account=None,
        extra_debit=None,
    ):
        entry = JournalEntry.all_objects.create(
            tenant=tenant,
            entry_number=number,
            entry_date=date(2026, 6, 1),
            description=f'{number} VIN in ref',
            reference=reference,
            status=status,
            reversed_entry=reversed_entry,
            created_by=user,
        )
        debit = construction_debit
        credit = credit_construction if credit_construction is not None else Decimal('0.00')
        if debit or credit:
            JournalEntryLine.objects.create(
                journal_entry=entry,
                account=accounts['0373'],
                debit_amount=debit,
                credit_amount=credit,
            )
        cash = extra_debit if extra_debit is not None else (credit or debit)
        cash_account = accounts[extra_debit_account] if extra_debit_account else accounts['1000']
        if extra_debit_account:
            JournalEntryLine.objects.create(
                journal_entry=entry,
                account=cash_account,
                debit_amount=extra_debit,
                credit_amount=Decimal('0.00'),
            )
            JournalEntryLine.objects.create(
                journal_entry=entry,
                account=accounts['1000'],
                debit_amount=Decimal('0.00'),
                credit_amount=extra_debit,
            )
        elif cash:
            JournalEntryLine.objects.create(
                journal_entry=entry,
                account=accounts['1000'],
                debit_amount=credit,
                credit_amount=debit,
            )
        return entry

    def _snapshot(self):
        return [
            (self.ppmv_original.pk, AssetJournalLinkRole.DEPENDENT_COST),
            (self.ppmv_storno.pk, AssetJournalLinkRole.DEPENDENT_COST),
            (self.ppmv_obveza.pk, AssetJournalLinkRole.DEPENDENT_COST),
            (self.ppmv_uplata.pk, AssetJournalLinkRole.PAYMENT),
            (self.fzoeu_vozila.pk, AssetJournalLinkRole.DEPENDENT_COST),
            (self.fzoeu_gume.pk, AssetJournalLinkRole.DEPENDENT_COST),
        ]

    def test_plan_does_not_write(self):
        before = FixedAssetJournalLink.all_objects.count()
        plan = plan_asset_journal_links(self.tenant, self.asset.pk)
        self.assertEqual(FixedAssetJournalLink.all_objects.count(), before)
        roles = {row.entry_number: row.proposed_role for row in plan.candidates}
        self.assertEqual(len(plan.candidates), 6)
        self.assertEqual(roles['202606-0011'], 'dependent_cost')
        self.assertEqual(roles['202606-0010'], 'dependent_cost')
        self.assertEqual(roles['202606-0010-ST'], 'dependent_cost')
        self.assertEqual(roles['202606-0012'], 'payment')
        self.assertEqual(roles['202607-0034'], 'dependent_cost')
        self.assertEqual(roles['202607-0035'], 'dependent_cost')
        self.assertTrue(any(item['reason'] == 'purchase_journal_entry' for item in plan.excluded))
        self.assertNotIn(self.unrelated.pk, {row.journal_entry_id for row in plan.candidates})

    def test_apply_without_snapshot_rejected(self):
        with self.assertRaises(ValidationError):
            apply_asset_journal_links(
                self.tenant,
                self.asset.pk,
                entries=[],
                case_id='A8-GOLF',
                reason='backfill golf links',
                user=self.user,
            )

    def test_apply_snapshot_creates_six_and_is_idempotent(self):
        first = apply_asset_journal_links(
            self.tenant,
            self.asset.pk,
            entries=self._snapshot(),
            case_id='A8-GOLF',
            reason='backfill golf links',
            user=self.user,
        )
        self.assertEqual(len(first['created']), 6)
        self.assertEqual(FixedAssetJournalLink.all_objects.filter(fixed_asset=self.asset).count(), 6)
        second = apply_asset_journal_links(
            self.tenant,
            self.asset.pk,
            entries=self._snapshot(),
            case_id='A8-GOLF',
            reason='backfill golf links again',
            user=self.user,
        )
        self.assertEqual(second['created'], [])
        self.assertEqual(len(second['existing']), 6)
        self.assertEqual(FixedAssetJournalLink.all_objects.filter(fixed_asset=self.asset).count(), 6)
        logs = AuditLog.all_objects.filter(action='fixed_asset_journal_link_backfill')
        self.assertEqual(logs.count(), 2)

    def test_apply_rejects_other_tenant_and_purchase_je(self):
        other_je = JournalEntry.all_objects.filter(tenant=self.other).first()
        with self.assertRaises(ValidationError):
            apply_asset_journal_links(
                self.tenant,
                self.asset.pk,
                entries=[(other_je.pk, AssetJournalLinkRole.OTHER)],
                case_id='A8-GOLF',
                reason='should fail tenant',
                user=self.user,
            )
        with self.assertRaises(ValidationError):
            apply_asset_journal_links(
                self.tenant,
                self.asset.pk,
                entries=[(self.purchase.pk, AssetJournalLinkRole.DEPENDENT_COST)],
                case_id='A8-GOLF',
                reason='should fail purchase fk',
                user=self.user,
            )

    def test_verify_balanced_after_apply(self):
        apply_asset_journal_links(
            self.tenant,
            self.asset.pk,
            entries=self._snapshot(),
            case_id='A8-GOLF',
            reason='backfill golf links',
            user=self.user,
        )
        result = verify_asset_journal_links(self.tenant, self.asset.pk)
        self.assertTrue(result['ok'])

    def test_verify_fails_on_mismatch(self):
        apply_asset_journal_links(
            self.tenant,
            self.asset.pk,
            entries=self._snapshot(),
            case_id='A8-GOLF',
            reason='backfill golf links',
            user=self.user,
        )
        extra = self._je(
            self.tenant,
            self.user,
            '202608-DRIFT',
            'FZOEU-VOZILA|WVWZZZCD8PW153457|DRIFT',
            Decimal('50.00'),
            self.accounts,
        )
        FixedAssetJournalLink.all_objects.create(
            tenant=self.tenant,
            fixed_asset=self.asset,
            journal_entry=extra,
            role=AssetJournalLinkRole.DEPENDENT_COST,
        )
        result = verify_asset_journal_links(self.tenant, self.asset.pk)
        self.assertFalse(result['ok'])
        with self.assertRaises(CommandError):
            call_command(
                'verify_asset_journal_links',
                tenant='linkbf',
                asset_id=self.asset.pk,
                stdout=StringIO(),
            )

    def test_execute_command_requires_snapshot(self):
        with self.assertRaises(CommandError):
            call_command(
                'execute_asset_journal_links',
                tenant='linkbf',
                asset_id=self.asset.pk,
                case_id='A8-GOLF',
                reason='missing snapshot',
                stdout=StringIO(),
            )
