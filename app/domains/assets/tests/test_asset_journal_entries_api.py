"""Read-only asset journal-entries API — composition, recon set, 405."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

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
from domains.assets.read.dto import (
    ASSET_JOURNAL_ENTRY_KEYS,
    CAPITALIZATION_RECONCILIATION_KEYS,
)
from tenants.models import Tenant, TenantMembership

HOST = 'assetje.racunai.hr'
OTHER_HOST = 'assetjeother.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class AssetJournalEntriesApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.tenant = Tenant.objects.create(slug='assetje', name='Asset JE Co')
        cls.other = Tenant.objects.create(slug='assetjeother', name='Other JE Co')
        cls.viewer = User.objects.create_user(username='aje-viewer', password='test')
        cls.accountant = User.objects.create_user(username='aje-acc', password='test')
        cls.outsider = User.objects.create_user(username='aje-out', password='test')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')
        TenantMembership.objects.create(user=cls.outsider, tenant=cls.other, role='viewer')
        cls.accounts = cls._accounts(cls.tenant)
        other_accounts = cls._accounts(cls.other)

        cls.purchase = cls._je(cls.tenant, cls.accountant, '202605-0012', date(2026, 5, 27), 'posted')
        cls._line(cls.purchase, cls.accounts['0373'], Decimal('15882.35'), Decimal('0'))
        cls._line(cls.purchase, cls.accounts['1000'], Decimal('0'), Decimal('15882.35'))

        cls.ppmv_obveza = cls._je(cls.tenant, cls.accountant, '202606-0011', date(2026, 6, 17), 'posted')
        cls._line(cls.ppmv_obveza, cls.accounts['0373'], Decimal('1051.04'), Decimal('0'))
        cls._line(cls.ppmv_obveza, cls.accounts['2201'], Decimal('0'), Decimal('1051.04'))

        cls.ppmv_original = cls._je(cls.tenant, cls.accountant, '202606-0010', date(2026, 6, 19), 'reversed')
        cls._line(cls.ppmv_original, cls.accounts['0373'], Decimal('1051.04'), Decimal('0'))
        cls._line(cls.ppmv_original, cls.accounts['1000'], Decimal('0'), Decimal('1051.04'))

        cls.ppmv_storno = cls._je(
            cls.tenant,
            cls.accountant,
            '202606-0010-ST',
            date(2026, 7, 3),
            'posted',
            reversed_entry=cls.ppmv_original,
        )
        cls._line(cls.ppmv_storno, cls.accounts['0373'], Decimal('0'), Decimal('1051.04'))
        cls._line(cls.ppmv_storno, cls.accounts['1000'], Decimal('1051.04'), Decimal('0'))

        cls.ppmv_uplata = cls._je(cls.tenant, cls.accountant, '202606-0012', date(2026, 6, 19), 'posted')
        cls._line(cls.ppmv_uplata, cls.accounts['2201'], Decimal('1051.04'), Decimal('0'))
        cls._line(cls.ppmv_uplata, cls.accounts['1000'], Decimal('0'), Decimal('1051.04'))

        cls.fzoeu_vozila = cls._je(cls.tenant, cls.accountant, '202607-0034', date(2026, 7, 3), 'posted')
        cls._line(cls.fzoeu_vozila, cls.accounts['0373'], Decimal('112.80'), Decimal('0'))
        cls._line(cls.fzoeu_vozila, cls.accounts['1000'], Decimal('0'), Decimal('112.80'))

        cls.fzoeu_gume = cls._je(cls.tenant, cls.accountant, '202607-0035', date(2026, 7, 3), 'posted')
        cls._line(cls.fzoeu_gume, cls.accounts['0373'], Decimal('3.60'), Decimal('0'))
        cls._line(cls.fzoeu_gume, cls.accounts['1000'], Decimal('0'), Decimal('3.60'))

        cls.asset = cls._asset(
            cls.tenant,
            cls.accounts,
            name='VW Golf',
            vin='WVWZZZCD8PW153457',
            cost=Decimal('17049.79'),
            purchase=cls.purchase,
        )
        for entry, role in (
            (cls.ppmv_original, AssetJournalLinkRole.DEPENDENT_COST),
            (cls.ppmv_storno, AssetJournalLinkRole.DEPENDENT_COST),
            (cls.ppmv_obveza, AssetJournalLinkRole.DEPENDENT_COST),
            (cls.ppmv_uplata, AssetJournalLinkRole.PAYMENT),
            (cls.fzoeu_vozila, AssetJournalLinkRole.DEPENDENT_COST),
            (cls.fzoeu_gume, AssetJournalLinkRole.DEPENDENT_COST),
        ):
            FixedAssetJournalLink.all_objects.create(
                tenant=cls.tenant,
                fixed_asset=cls.asset,
                journal_entry=entry,
                role=role,
            )

        cls.other_purchase = cls._je(cls.other, cls.outsider, 'OTHER-P', date(2026, 1, 1), 'posted')
        cls.other_asset = cls._asset(
            cls.other,
            other_accounts,
            name='Tuđe',
            vin='',
            cost=Decimal('10.00'),
            purchase=cls.other_purchase,
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
    def _je(tenant, user, number, entry_date, status, reversed_entry=None):
        return JournalEntry.all_objects.create(
            tenant=tenant,
            entry_number=number,
            entry_date=entry_date,
            description=number,
            status=status,
            reversed_entry=reversed_entry,
            created_by=user,
        )

    @staticmethod
    def _line(entry, account, debit, credit):
        return JournalEntryLine.objects.create(
            journal_entry=entry,
            account=account,
            debit_amount=debit,
            credit_amount=credit,
        )

    @staticmethod
    def _asset(tenant, accounts, *, name, vin, cost, purchase, activation=None):
        return FixedAsset.all_objects.create(
            tenant=tenant,
            name=name,
            inventory_number='OS',
            vin=vin,
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=cost,
            purchase_date=date(2026, 5, 27),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=accounts['0373'],
            asset_account=accounts['032001'],
            accumulated_depreciation_account=accounts['0393'],
            depreciation_expense_account=accounts['4314'],
            purchase_journal_entry=purchase,
            activation_journal_entry=activation,
        )

    def _client(self, user, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def _url(self, asset_id=None):
        return f'/api/assets/fixed-assets/{asset_id or self.asset.pk}/journal-entries/'

    def test_unauthenticated_is_401(self):
        client = APIClient()
        client.defaults['HTTP_HOST'] = HOST
        self.assertEqual(client.get(self._url()).status_code, 401)

    def test_other_tenant_asset_is_404(self):
        response = self._client(self.viewer).get(self._url(self.other_asset.pk))
        self.assertEqual(response.status_code, 404)

    def test_write_methods_are_405(self):
        client = self._client(self.accountant)
        for method in ('post', 'patch', 'put', 'delete'):
            response = getattr(client, method)(self._url())
            self.assertEqual(response.status_code, 405, method)

    def test_allowlist_and_golf_composition(self):
        response = self._client(self.viewer).get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.data['results']
        self.assertEqual(len(rows), 7)
        self.assertEqual(set(rows[0]), set(ASSET_JOURNAL_ENTRY_KEYS))
        self.assertEqual(set(response.data['reconciliation']), set(CAPITALIZATION_RECONCILIATION_KEYS))
        numbers = [row['entry_number'] for row in rows]
        self.assertEqual(
            numbers,
            [
                '202605-0012',
                '202606-0011',
                '202606-0010',
                '202606-0012',
                '202606-0010-ST',
                '202607-0034',
                '202607-0035',
            ],
        )
        by_number = {row['entry_number']: row for row in rows}
        self.assertEqual(by_number['202605-0012']['role'], 'purchase')
        self.assertEqual(by_number['202606-0011']['role'], 'dependent_cost')
        self.assertEqual(by_number['202606-0012']['role'], 'payment')
        self.assertEqual(by_number['202606-0010']['audit_kind'], 'reversed')
        self.assertEqual(by_number['202606-0010-ST']['audit_kind'], 'storno')
        self.assertEqual(by_number['202606-0010']['capitalized_amount'], '0.00')
        self.assertEqual(by_number['202606-0010-ST']['capitalized_amount'], '0.00')
        self.assertIsNone(by_number['202606-0012']['capitalized_amount'])
        recon = response.data['reconciliation']
        self.assertEqual(recon['capitalized_net'], '17049.79')
        self.assertEqual(recon['acquisition_cost'], '17049.79')
        self.assertEqual(recon['difference'], '0.00')
        self.assertTrue(recon['balanced'])

    def test_payment_and_depreciation_excluded_from_capitalized_net(self):
        dep_je = self._je(self.tenant, self.accountant, '202608-DEP', date(2026, 8, 1), 'posted')
        self._line(dep_je, self.accounts['4314'], Decimal('100.00'), Decimal('0'))
        self._line(dep_je, self.accounts['0393'], Decimal('0'), Decimal('100.00'))
        DepreciationSchedule.all_objects.create(
            tenant=self.tenant,
            fixed_asset=self.asset,
            year=2026,
            month=8,
            amount=Decimal('100.00'),
            journal_entry=dep_je,
        )
        response = self._client(self.viewer).get(self._url())
        self.assertEqual(response.status_code, 200)
        dep = next(row for row in response.data['results'] if row['role'] == 'depreciation')
        self.assertIsNone(dep['capitalized_amount'])
        self.assertTrue(response.data['reconciliation']['balanced'])
        self.assertEqual(response.data['reconciliation']['capitalized_net'], '17049.79')

    def test_activation_does_not_zero_capitalized_net(self):
        activation = self._je(self.tenant, self.accountant, '202608-ACT', date(2026, 8, 1), 'posted')
        self._line(activation, self.accounts['032001'], Decimal('17049.79'), Decimal('0'))
        self._line(activation, self.accounts['0373'], Decimal('0'), Decimal('17049.79'))
        self.asset.activation_journal_entry = activation
        self.asset.status = FixedAssetStatus.ACTIVE
        self.asset.save(update_fields=['activation_journal_entry', 'status'])

        response = self._client(self.viewer).get(self._url())
        self.assertEqual(response.status_code, 200)
        act = next(row for row in response.data['results'] if row['role'] == 'activation')
        self.assertIsNone(act['capitalized_amount'])
        recon = response.data['reconciliation']
        self.assertEqual(recon['capitalized_net'], '17049.79')
        self.assertTrue(recon['balanced'])

    def test_query_count_is_bounded(self):
        client = self._client(self.viewer)
        with self.assertNumQueries(8):
            first = client.get(self._url())
        self.assertEqual(first.status_code, 200)
        extra = self._je(self.tenant, self.accountant, '202608-EXTRA', date(2026, 8, 2), 'posted')
        self._line(extra, self.accounts['0373'], Decimal('1.00'), Decimal('0'))
        self._line(extra, self.accounts['1000'], Decimal('0'), Decimal('1.00'))
        FixedAssetJournalLink.all_objects.create(
            tenant=self.tenant,
            fixed_asset=self.asset,
            journal_entry=extra,
            role=AssetJournalLinkRole.DEPENDENT_COST,
        )
        with self.assertNumQueries(8):
            second = client.get(self._url())
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.data['results']), 8)
