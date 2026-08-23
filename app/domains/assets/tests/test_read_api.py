"""Read-only Assets API — tenant isolation, allowlist, query bound, 405."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import (
    AccountType,
    ChartOfAccounts,
    DepreciationMethod,
    DepreciationSchedule,
    FixedAsset,
    FixedAssetOrigin,
    FixedAssetStatus,
    JournalEntry,
)
from domains.assets.read.dto import DETAIL_KEYS, LIST_ITEM_KEYS, SCHEDULE_ITEM_KEYS
from tenants.models import Tenant, TenantMembership

HOST = 'assetsread.racunai.hr'
OTHER_HOST = 'assetsother.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class AssetsReadApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.tenant = Tenant.objects.create(slug='assetsread', name='Assets Read Co')
        cls.other = Tenant.objects.create(slug='assetsother', name='Assets Other Co')
        cls.viewer = User.objects.create_user(username='assets-viewer', password='test')
        cls.accountant = User.objects.create_user(username='assets-acc', password='test')
        cls.outsider = User.objects.create_user(username='assets-outsider', password='test')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')
        TenantMembership.objects.create(user=cls.outsider, tenant=cls.other, role='viewer')

        cls.accounts = cls._accounts(cls.tenant)
        other_accounts = cls._accounts(cls.other)

        cls.purchase = cls._posted_je(cls.tenant, cls.accountant, '202607-FA1')
        cls.activation = cls._posted_je(cls.tenant, cls.accountant, '202607-ACT')
        cls.dep_je = cls._posted_je(cls.tenant, cls.accountant, '202607-DEP')
        other_purchase = cls._posted_je(cls.other, cls.outsider, 'OTHER-FA')

        cls.asset = cls._asset(
            cls.tenant,
            name='VW T-Cross',
            inventory_number='OS-001',
            vin='WVGZZZC1ZPY022544',
            status=FixedAssetStatus.ACTIVE,
            purchase_date=date(2026, 5, 22),
            in_service_date=date(2026, 7, 1),
            acquisition_cost=Decimal('8000.00'),
            useful_life_months=60,
            accounts=cls.accounts,
            purchase_je=cls.purchase,
            activation_je=cls.activation,
        )
        cls.prep = cls._asset(
            cls.tenant,
            name='VW Golf',
            inventory_number='OS-002',
            vin='WVWZZZCD8PW153457',
            status=FixedAssetStatus.IN_PREPARATION,
            purchase_date=date(2026, 5, 27),
            in_service_date=None,
            acquisition_cost=Decimal('15882.35'),
            useful_life_months=None,
            accounts=cls.accounts,
            purchase_je=cls._posted_je(cls.tenant, cls.accountant, '202605-GOLF'),
        )
        cls.disposed = cls._asset(
            cls.tenant,
            name='Star stroj',
            inventory_number='OS-003',
            vin='',
            status=FixedAssetStatus.DISPOSED,
            purchase_date=date(2020, 1, 15),
            in_service_date=date(2020, 2, 1),
            acquisition_cost=Decimal('1000.00'),
            useful_life_months=12,
            accounts=cls.accounts,
            purchase_je=cls._posted_je(cls.tenant, cls.accountant, '202001-OLD'),
        )
        cls.other_asset = cls._asset(
            cls.other,
            name='Tuđe sredstvo',
            inventory_number='XX-1',
            vin='',
            status=FixedAssetStatus.ACTIVE,
            purchase_date=date(2026, 1, 1),
            in_service_date=date(2026, 1, 1),
            acquisition_cost=Decimal('500.00'),
            useful_life_months=24,
            accounts=other_accounts,
            purchase_je=other_purchase,
        )
        cls.july = DepreciationSchedule.all_objects.create(
            tenant=cls.tenant,
            fixed_asset=cls.asset,
            year=2026,
            month=7,
            amount=Decimal('133.33'),
            journal_entry=cls.dep_je,
        )
        cls.august = DepreciationSchedule.all_objects.create(
            tenant=cls.tenant,
            fixed_asset=cls.asset,
            year=2026,
            month=8,
            amount=Decimal('133.33'),
        )

    @staticmethod
    def _accounts(tenant):
        account_type = AccountType.all_objects.create(tenant=tenant, name='asset')
        codes = ('0373', '032001', '0393', '4314')
        return {
            code: ChartOfAccounts.all_objects.create(
                tenant=tenant,
                account_code=code,
                account_name=code,
                account_type=account_type,
                is_postable=True,
            )
            for code in codes
        }

    @staticmethod
    def _posted_je(tenant, user, number):
        return JournalEntry.all_objects.create(
            tenant=tenant,
            entry_number=number,
            entry_date=date(2026, 7, 1),
            description=number,
            status='posted',
            created_by=user,
        )

    @staticmethod
    def _asset(
        tenant,
        *,
        name,
        inventory_number,
        vin,
        status,
        purchase_date,
        in_service_date,
        acquisition_cost,
        useful_life_months,
        accounts,
        purchase_je,
        activation_je=None,
    ):
        return FixedAsset.all_objects.create(
            tenant=tenant,
            name=name,
            inventory_number=inventory_number,
            vin=vin,
            status=status,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=acquisition_cost,
            purchase_date=purchase_date,
            in_service_date=in_service_date,
            useful_life_months=useful_life_months,
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=accounts['0373'],
            asset_account=accounts['032001'],
            accumulated_depreciation_account=accounts['0393'],
            depreciation_expense_account=accounts['4314'],
            purchase_journal_entry=purchase_je,
            activation_journal_entry=activation_je,
        )

    def _client(self, user, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def test_unauthenticated_is_401(self):
        client = APIClient()
        client.defaults['HTTP_HOST'] = HOST
        response = client.get('/api/assets/fixed-assets/')
        self.assertEqual(response.status_code, 401)

    def test_list_returns_own_tenant_only(self):
        response = self._client(self.viewer).get('/api/assets/fixed-assets/')
        self.assertEqual(response.status_code, 200)
        ids = {row['id'] for row in response.data['results']}
        self.assertEqual(ids, {self.asset.pk, self.prep.pk, self.disposed.pk})
        self.assertNotIn(self.other_asset.pk, ids)

        other = self._client(self.outsider, host=OTHER_HOST).get('/api/assets/fixed-assets/')
        self.assertEqual(other.status_code, 200)
        other_ids = {row['id'] for row in other.data['results']}
        self.assertEqual(other_ids, {self.other_asset.pk})

    def test_list_item_allowlist(self):
        response = self._client(self.viewer).get('/api/assets/fixed-assets/')
        row = next(item for item in response.data['results'] if item['id'] == self.asset.pk)
        self.assertEqual(set(row), set(LIST_ITEM_KEYS))
        self.assertEqual(row['name'], 'VW T-Cross')
        self.assertEqual(row['activation_date'], '2026-07-01')
        self.assertEqual(row['acquisition_cost'], '8000.00')
        self.assertEqual(row['accumulated_depreciation'], '133.33')
        self.assertEqual(row['current_book_value'], '7866.67')
        self.assertNotIn('vin', row)
        self.assertNotIn('construction_account', row)

    def test_list_deterministic_order(self):
        response = self._client(self.viewer).get('/api/assets/fixed-assets/')
        names = [row['name'] for row in response.data['results']]
        self.assertEqual(names, ['VW Golf', 'VW T-Cross', 'Star stroj'])

    def test_list_status_filter(self):
        response = self._client(self.viewer).get(
            '/api/assets/fixed-assets/',
            {'status': 'in_preparation'},
        )
        self.assertEqual(response.status_code, 200)
        ids = {row['id'] for row in response.data['results']}
        self.assertEqual(ids, {self.prep.pk})

    def test_list_unknown_status_is_400(self):
        response = self._client(self.viewer).get(
            '/api/assets/fixed-assets/',
            {'status': 'ready'},
        )
        self.assertEqual(response.status_code, 400)

    def test_list_search(self):
        response = self._client(self.viewer).get(
            '/api/assets/fixed-assets/',
            {'search': 'T-Cross'},
        )
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], self.asset.pk)

    def test_list_query_count_is_bounded(self):
        client = self._client(self.viewer)
        with self.assertNumQueries(6):
            first = client.get('/api/assets/fixed-assets/')
        self.assertEqual(first.status_code, 200)
        extra_je = self._posted_je(self.tenant, self.accountant, '202608-EXTRA')
        self._asset(
            self.tenant,
            name='Dodatno',
            inventory_number='OS-099',
            vin='',
            status=FixedAssetStatus.IN_PREPARATION,
            purchase_date=date(2026, 6, 1),
            in_service_date=None,
            acquisition_cost=Decimal('100.00'),
            useful_life_months=None,
            accounts=self.accounts,
            purchase_je=extra_je,
        )
        with self.assertNumQueries(6):
            second = client.get('/api/assets/fixed-assets/')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['count'], 4)

    def test_detail_allowlist_and_activation_je(self):
        response = self._client(self.viewer).get(f'/api/assets/fixed-assets/{self.asset.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), set(DETAIL_KEYS))
        self.assertEqual(response.data['vin'], 'WVGZZZC1ZPY022544')
        self.assertEqual(response.data['useful_life_months'], 60)
        self.assertEqual(response.data['depreciation_method'], 'linear')
        self.assertEqual(response.data['activation_journal_entry_id'], self.activation.pk)
        self.assertNotIn('activation_readiness', response.data)
        self.assertNotIn('asset_account', response.data)

    def test_detail_other_tenant_is_404(self):
        response = self._client(self.viewer).get(
            f'/api/assets/fixed-assets/{self.other_asset.pk}/',
        )
        self.assertEqual(response.status_code, 404)

    def test_schedule_other_tenant_is_404(self):
        response = self._client(self.viewer).get(
            f'/api/assets/fixed-assets/{self.other_asset.pk}/depreciation-schedule/',
        )
        self.assertEqual(response.status_code, 404)

    def test_schedule_allowlist_running_totals_and_posted(self):
        response = self._client(self.viewer).get(
            f'/api/assets/fixed-assets/{self.asset.pk}/depreciation-schedule/',
        )
        self.assertEqual(response.status_code, 200)
        rows = response.data['results']
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows[0]), set(SCHEDULE_ITEM_KEYS))
        self.assertEqual(rows[0]['year'], 2026)
        self.assertEqual(rows[0]['month'], 7)
        self.assertEqual(rows[0]['depreciation_amount'], '133.33')
        self.assertTrue(rows[0]['posted'])
        self.assertEqual(rows[0]['journal_entry_id'], self.dep_je.pk)
        self.assertEqual(rows[0]['accumulated_depreciation'], '133.33')
        self.assertEqual(rows[0]['book_value_after'], '7866.67')
        self.assertEqual(rows[1]['month'], 8)
        self.assertFalse(rows[1]['posted'])
        self.assertIsNone(rows[1]['journal_entry_id'])
        self.assertEqual(rows[1]['accumulated_depreciation'], '133.33')
        self.assertEqual(rows[1]['book_value_after'], '7733.34')

    def test_schedule_empty_for_preparation_asset(self):
        response = self._client(self.viewer).get(
            f'/api/assets/fixed-assets/{self.prep.pk}/depreciation-schedule/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])

    def test_post_is_405(self):
        client = self._client(self.accountant)
        self.assertEqual(client.post('/api/assets/fixed-assets/').status_code, 405)
        self.assertEqual(
            client.post(f'/api/assets/fixed-assets/{self.asset.pk}/').status_code,
            405,
        )
        self.assertEqual(
            client.post(
                f'/api/assets/fixed-assets/{self.asset.pk}/depreciation-schedule/',
            ).status_code,
            405,
        )
