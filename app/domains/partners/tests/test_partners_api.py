"""Acceptance tests for ADR-0022 partners MDM JWT API."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from partners.models import Partner, PartnerBankAccount, PartnerContact
from tenants.models import Tenant, TenantMembership

HOST = 'partners.racunai.hr'
OTHER_HOST = 'partners2.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class PartnersApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='partners', name='Partners Co')
        cls.other = Tenant.objects.create(slug='partners2', name='Other Partners Co')

        User = get_user_model()
        cls.owner = User.objects.create_user(username='p-owner', password='test')
        cls.accountant = User.objects.create_user(username='p-acct', password='test')
        cls.viewer = User.objects.create_user(username='p-viewer', password='test')
        cls.outsider = User.objects.create_user(username='p-out', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')

        cls.active = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Active Partner',
            partner_type='customer',
            status='active',
            tax_number='11111111111',
            address='Ulica 1',
            city='Zagreb',
            postal_code='10000',
        )
        cls.inactive = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Inactive Partner',
            partner_type='supplier',
            status='inactive',
            tax_number='22222222222',
            address='Ulica 2',
            city='Split',
            postal_code='21000',
        )
        cls.blocked = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Blocked Partner',
            partner_type='both',
            status='blocked',
            tax_number='33333333333',
            address='Ulica 3',
            city='Rijeka',
            postal_code='51000',
        )
        cls.other_partner = Partner.all_objects.create(
            tenant=cls.other,
            name='Other Tenant Partner',
            partner_type='customer',
            status='active',
            tax_number='11111111111',
            address='Other',
            city='Osijek',
            postal_code='31000',
        )

    def _client(self, user, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def test_unauthenticated_returns_401_bearer(self):
        client = APIClient()
        client.defaults['HTTP_HOST'] = HOST
        response = client.get('/api/partners/')
        self.assertEqual(response.status_code, 401)
        self.assertIn('Bearer', response.get('WWW-Authenticate', ''))

    def test_outsider_gets_404(self):
        client = self._client(self.outsider)
        response = client.get('/api/partners/')
        self.assertEqual(response.status_code, 404)

    def test_default_list_is_active_only(self):
        client = self._client(self.viewer)
        response = client.get('/api/partners/')
        self.assertEqual(response.status_code, 200)
        ids = {row['id'] for row in response.data['results']}
        self.assertEqual(ids, {self.active.pk})

    def test_filter_all_includes_inactive_and_blocked(self):
        client = self._client(self.viewer)
        response = client.get('/api/partners/', {'filter': 'all'})
        self.assertEqual(response.status_code, 200)
        ids = {row['id'] for row in response.data['results']}
        self.assertEqual(ids, {self.active.pk, self.inactive.pk, self.blocked.pk})

    def test_filter_inactive(self):
        client = self._client(self.viewer)
        response = client.get('/api/partners/', {'filter': 'inactive'})
        self.assertEqual(response.status_code, 200)
        ids = {row['id'] for row in response.data['results']}
        self.assertEqual(ids, {self.inactive.pk, self.blocked.pk})

    def test_cross_tenant_partner_404(self):
        client = self._client(self.owner)
        response = client.get(f'/api/partners/{self.other_partner.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_delete_partner_not_allowed(self):
        client = self._client(self.owner)
        response = client.delete(f'/api/partners/{self.active.pk}/')
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Partner.all_objects.filter(pk=self.active.pk).exists())

    def test_viewer_cannot_create(self):
        client = self._client(self.viewer)
        response = client.post(
            '/api/partners/',
            {
                'name': 'New Co',
                'partner_type': 'customer',
                'tax_number': '44444444444',
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 404)

    def test_create_and_oib_conflict(self):
        client = self._client(self.accountant)
        payload = {
            'name': 'Dup OIB',
            'partner_type': 'customer',
            'tax_number': self.active.tax_number,
            'address': 'A',
            'city': 'Zagreb',
            'postal_code': '10000',
        }
        response = client.post('/api/partners/', payload, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'partner_tax_number_conflict')
        self.assertEqual(response.data['field'], 'tax_number')

        payload['tax_number'] = '55555555555'
        response = client.post('/api/partners/', payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['tax_number'], '55555555555')
        self.assertEqual(response.data['status'], 'active')

    def test_patch_status_inactive(self):
        client = self._client(self.owner)
        response = client.patch(
            f'/api/partners/{self.active.pk}/',
            {'status': 'inactive'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'inactive')

    def test_contacts_primary_is_atomic(self):
        client = self._client(self.owner)
        c1 = client.post(
            f'/api/partners/{self.active.pk}/contacts/',
            {'first_name': 'Ana', 'last_name': 'A', 'is_primary': True},
            format='json',
        )
        self.assertEqual(c1.status_code, 201)
        c2 = client.post(
            f'/api/partners/{self.active.pk}/contacts/',
            {'first_name': 'Boris', 'last_name': 'B', 'is_primary': True},
            format='json',
        )
        self.assertEqual(c2.status_code, 201)
        self.assertTrue(c2.data['is_primary'])
        first = PartnerContact.objects.get(pk=c1.data['id'])
        self.assertFalse(first.is_primary)

    def test_iban_conflict_and_primary(self):
        client = self._client(self.owner)
        a1 = client.post(
            f'/api/partners/{self.active.pk}/bank-accounts/',
            {
                'bank_name': 'OTP',
                'bic': 'OTPHRH2X',
                'iban': 'HR12 1234 5678 9012 3456 7',
                'is_primary': True,
            },
            format='json',
        )
        self.assertEqual(a1.status_code, 201)
        self.assertEqual(a1.data['iban'], 'HR1212345678901234567')

        dup = client.post(
            f'/api/partners/{self.active.pk}/bank-accounts/',
            {
                'bank_name': 'PBZ',
                'bic': 'PBZGHR2X',
                'iban': 'hr1212345678901234567',
            },
            format='json',
        )
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(dup.data['code'], 'partner_iban_conflict')

        a2 = client.post(
            f'/api/partners/{self.active.pk}/bank-accounts/',
            {
                'bank_name': 'PBZ',
                'bic': 'PBZGHR2X',
                'iban': 'HR9876543210987654321',
                'is_primary': True,
            },
            format='json',
        )
        self.assertEqual(a2.status_code, 201)
        first = PartnerBankAccount.objects.get(pk=a1.data['id'])
        self.assertFalse(first.is_primary)
        self.assertTrue(PartnerBankAccount.objects.get(pk=a2.data['id']).is_primary)

    def test_delete_contact(self):
        contact = PartnerContact.objects.create(
            partner=self.active,
            first_name='X',
            last_name='Y',
        )
        client = self._client(self.owner)
        response = client.delete(f'/api/partners/{self.active.pk}/contacts/{contact.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(PartnerContact.objects.filter(pk=contact.pk).exists())
