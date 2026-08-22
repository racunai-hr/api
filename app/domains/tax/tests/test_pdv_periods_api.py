"""Acceptance tests for GET /api/tax/pdv/periods/."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import VATLedgerEntry, VATPeriod, VATReturn, VATReturnStatus
from tenants.models import Tenant, TenantMembership

HOST = 'pdvread.racunai.hr'
OTHER_HOST = 'pdvother.racunai.hr'
EMPTY_HOST = 'pdvempty.racunai.hr'

PERIOD_FIELDS = {
    'period',
    'period_status',
    'has_ledger',
    'return_version',
    'return_status',
    'vat_due',
    'submitted_at',
}


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, EMPTY_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class PdvPeriodsApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='pdvread', name='PDV Read Co')
        cls.other = Tenant.objects.create(slug='pdvother', name='Other PDV Co')
        cls.empty = Tenant.objects.create(slug='pdvempty', name='Empty PDV Co')
        User = get_user_model()
        cls.viewer = User.objects.create_user(username='pdv-viewer', password='test')
        cls.accountant = User.objects.create_user(username='pdv-accountant', password='test')
        cls.owner = User.objects.create_user(username='pdv-owner', password='test')
        cls.outsider = User.objects.create_user(username='pdv-outsider', password='test')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        TenantMembership.objects.create(user=cls.outsider, tenant=cls.other, role='viewer')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.empty, role='viewer')

        cls.aug = VATPeriod.all_objects.create(tenant=cls.tenant, year=2026, month=8, status='open')
        cls.jul = VATPeriod.all_objects.create(
            tenant=cls.tenant,
            year=2026,
            month=7,
            status='submitted',
            submitted_at=timezone.now(),
        )
        cls.dec = VATPeriod.all_objects.create(tenant=cls.tenant, year=2025, month=12, status='open')
        VATPeriod.all_objects.create(tenant=cls.other, year=2026, month=7, status='open')

        VATLedgerEntry.all_objects.create(
            tenant=cls.tenant,
            vat_period=cls.jul,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 7, 10),
            document_number='OUT-1',
            partner_name='Kupac',
            partner_oib='12345678901',
            base_amount=Decimal('100.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('25.00'),
            vat_box='203',
        )
        VATLedgerEntry.all_objects.create(
            tenant=cls.tenant,
            vat_period=cls.jul,
            ledger_type=VATLedgerEntry.LEDGER_U_RA,
            entry_date=date(2026, 7, 12),
            document_number='IN-1',
            partner_name='Dobavljač',
            partner_oib='98765432109',
            base_amount=Decimal('40.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('10.00'),
            vat_box='303',
        )

        VATReturn.all_objects.create(
            tenant=cls.tenant,
            vat_period=cls.jul,
            version=3,
            status=VATReturnStatus.GENERATED,
            payload_snapshot={'fields': {}},
            payload_hash='b' * 64,
            payload_json=ContentFile(b'{}', name='v3.json'),
        )
        VATReturn.all_objects.create(
            tenant=cls.tenant,
            vat_period=cls.jul,
            version=2,
            status=VATReturnStatus.SUBMITTED,
            payload_snapshot={'fields': {}},
            payload_hash='a' * 64,
            payload_json=ContentFile(b'{}', name='v2.json'),
        )

    def _auth_client(self, user=None, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user or self.viewer).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def test_anonymous_gets_401_bearer(self):
        client = APIClient()
        client.defaults['HTTP_HOST'] = HOST
        response = client.get('/api/tax/pdv/periods/')
        self.assertEqual(response.status_code, 401)
        self.assertIn('Bearer', response.get('WWW-Authenticate', ''))

    def test_outsider_on_tenant_host_gets_404(self):
        client = self._auth_client(user=self.outsider, host=HOST)
        response = client.get('/api/tax/pdv/periods/')
        self.assertEqual(response.status_code, 404)

    def test_viewer_accountant_owner_get_200(self):
        for user in (self.viewer, self.accountant, self.owner):
            response = self._auth_client(user=user).get('/api/tax/pdv/periods/')
            self.assertEqual(response.status_code, 200, user.username)

    def test_empty_tenant_returns_empty_list(self):
        client = self._auth_client(user=self.viewer, host=EMPTY_HOST)
        response = client.get('/api/tax/pdv/periods/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'count': 0, 'results': []})

    def test_tenant_isolation_and_ordering(self):
        response = self._auth_client().get('/api/tax/pdv/periods/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 3)
        periods = [row['period'] for row in data['results']]
        self.assertEqual(periods, ['2026-08', '2026-07', '2025-12'])

    def test_payload_has_no_id_and_uses_yyyy_mm(self):
        response = self._auth_client().get('/api/tax/pdv/periods/')
        july = next(row for row in response.json()['results'] if row['period'] == '2026-07')
        self.assertEqual(set(july.keys()), PERIOD_FIELDS)
        self.assertNotIn('id', july)
        self.assertEqual(july['has_ledger'], True)
        self.assertEqual(july['vat_due'], '15.00')
        self.assertEqual(july['return_version'], 2)
        self.assertEqual(july['return_status'], VATReturnStatus.SUBMITTED)
        self.assertEqual(july['period_status'], 'submitted')
        self.assertIsNotNone(july['submitted_at'])

        august = next(row for row in response.json()['results'] if row['period'] == '2026-08')
        self.assertEqual(august['has_ledger'], False)
        self.assertEqual(august['vat_due'], '0.00')
        self.assertIsNone(august['return_version'])
        self.assertIsNone(august['return_status'])
        self.assertIsNone(august['submitted_at'])
