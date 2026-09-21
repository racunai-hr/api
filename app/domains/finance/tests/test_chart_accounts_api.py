"""Chart-of-accounts list contract: count vs results slice, and search."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import AccountType, ChartOfAccounts
from tenants.models import Tenant, TenantMembership

HOST = 'coa.racunai.hr'
OTHER_HOST = 'coa2.racunai.hr'
SEARCH_HOST = 'coasearch.racunai.hr'
SEARCH_OTHER_HOST = 'coa2search.racunai.hr'
OVER_LIMIT = 501


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class ChartOfAccountsLimitContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='coa', name='COA Limit')
        cls.other = Tenant.objects.create(slug='coa2', name='COA Other')
        User = get_user_model()
        cls.owner = User.objects.create_user(username='coa-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')

        account_type = AccountType.all_objects.create(tenant=cls.tenant, name='expense')
        ChartOfAccounts.all_objects.bulk_create(
            [
                ChartOfAccounts(
                    tenant=cls.tenant,
                    account_code=f'{index:04d}',
                    account_name=f'Test konto {index:04d}',
                    account_type=account_type,
                    is_postable=True,
                    is_active=True,
                )
                for index in range(OVER_LIMIT)
            ]
        )
        other_type = AccountType.all_objects.create(tenant=cls.other, name='expense')
        ChartOfAccounts.all_objects.create(
            tenant=cls.other,
            account_code='4198',
            account_name='Tuđi konto',
            account_type=other_type,
            is_postable=True,
        )

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = HOST

    def test_unfiltered_list_caps_results_and_reports_true_count(self):
        response = self.client.get('/api/finance/chart-of-accounts/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], OVER_LIMIT)
        self.assertEqual(len(response.data['results']), 500)
        self.assertEqual(response.data['results'][0]['code'], '0000')
        self.assertEqual(response.data['results'][-1]['code'], '0499')
        listed_ids = {row['id'] for row in response.data['results']}
        other_ids = set(
            ChartOfAccounts.all_objects.filter(tenant=self.other).values_list('pk', flat=True)
        )
        self.assertFalse(listed_ids & other_ids)


@override_settings(
    ALLOWED_HOSTS=[SEARCH_HOST, SEARCH_OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class ChartOfAccountsSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='coasearch', name='COA Search')
        cls.other = Tenant.objects.create(slug='coa2search', name='COA Search Other')
        User = get_user_model()
        cls.owner = User.objects.create_user(username='coa-search-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')

        account_type = AccountType.all_objects.create(tenant=cls.tenant, name='expense')
        ChartOfAccounts.all_objects.create(
            tenant=cls.tenant,
            account_code='4100',
            account_name='Troškovi telefona, teleksa, telefaxa i sl.',
            account_type=account_type,
            is_postable=True,
        )
        ChartOfAccounts.all_objects.create(
            tenant=cls.tenant,
            account_code='4198',
            account_name='Troškovi posredovanja pri nabavi ili prodaji dobara i usluga',
            account_type=account_type,
            is_postable=True,
        )
        ChartOfAccounts.all_objects.create(
            tenant=cls.tenant,
            account_code='41',
            account_name='Troškovi usluga',
            account_type=account_type,
            is_postable=False,
            is_synthetic=True,
        )
        other_type = AccountType.all_objects.create(tenant=cls.other, name='expense')
        ChartOfAccounts.all_objects.create(
            tenant=cls.other,
            account_code='4198',
            account_name='Troškovi posredovanja pri nabavi ili prodaji dobara i usluga',
            account_type=other_type,
            is_postable=True,
        )

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = SEARCH_HOST

    def test_search_returns_matching_account_without_truncating_count(self):
        response = self.client.get('/api/finance/chart-of-accounts/?search=4198')
        self.assertEqual(response.status_code, 200)
        codes = [row['code'] for row in response.data['results']]
        self.assertEqual(codes, ['4198'])
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['count'], len(response.data['results']))
        self.assertEqual(
            response.data['results'][0]['name'],
            'Troškovi posredovanja pri nabavi ili prodaji dobara i usluga',
        )
        listed_ids = {row['id'] for row in response.data['results']}
        other_ids = set(
            ChartOfAccounts.all_objects.filter(tenant=self.other).values_list('pk', flat=True)
        )
        self.assertFalse(listed_ids & other_ids)
