"""Acceptance tests for ADR-0021 banking JWT read API (Slice 3)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from banking.models import BankImportRun, BankStatement, BankSyncRun, BankTransaction
from banking.provider_models import BankConnection, BankProvider, PaymentOrder
from domains.banking.read.balances import mask_iban
from domains.banking.read.dto import PAYMENT_ORDER_ALLOWLIST, payment_order_dto
from payments.models import BankAccount
from tenants.models import Tenant, TenantMembership

HOST = 'bankread.racunai.hr'
OTHER_HOST = 'bankother.racunai.hr'
IBAN = 'HR6124070001100204771'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class BankingReadApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='bankread', name='Bank Read Co')
        cls.other = Tenant.objects.create(slug='bankother', name='Other Bank Co')
        User = get_user_model()
        cls.viewer = User.objects.create_user(username='bank-viewer', password='test')
        cls.outsider = User.objects.create_user(username='bank-outsider', password='test')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        TenantMembership.objects.create(user=cls.outsider, tenant=cls.other, role='viewer')

        cls.account = BankAccount.all_objects.create(
            tenant=cls.tenant,
            account_name='Poslovni EUR',
            bank_name='OTP',
            account_number='0204771',
            iban=IBAN,
            currency='EUR',
        )
        cls.other_account = BankAccount.all_objects.create(
            tenant=cls.other,
            account_name='Other EUR',
            bank_name='OTP',
            account_number='999',
            iban='HR0012345678901234567',
            currency='EUR',
        )
        cls.statement = BankStatement.all_objects.create(
            tenant=cls.tenant,
            statement_number='ST-100',
            bank_account=cls.account,
            statement_date=date(2026, 8, 1),
            opening_balance=Decimal('1000.00'),
            closing_balance=Decimal('1100.00'),
            imported_by=cls.viewer,
        )
        cls.tx = BankTransaction.all_objects.create(
            tenant=cls.tenant,
            bank_statement=cls.statement,
            transaction_date=date(2026, 8, 1),
            amount=Decimal('100.00'),
            transaction_type='credit',
            description='Uplata',
            currency='EUR',
            match_status='unmatched',
            external_id='tx-1',
        )
        provider = BankProvider.objects.create(
            code='otp-bankread',
            name='OTP',
            environment='sandbox',
            iam_base='https://iam.example.test',
            api_base='https://api.example.test',
            service_host='api.example.test',
        )
        cls.connection = BankConnection.all_objects.create(
            tenant=cls.tenant,
            bank_provider=provider,
            bank_account=cls.account,
            status='connected',
            last_sync_at=timezone.now(),
        )
        cls.account.provider_connection = cls.connection
        cls.account.save(update_fields=['provider_connection'])

        cls.order = PaymentOrder.all_objects.create(
            tenant=cls.tenant,
            connection=cls.connection,
            status='draft',
            debtor_iban=IBAN,
            creditor_iban='HR9910090071100100001',
            creditor_name='Primatelj d.o.o.',
            amount=Decimal('42.50'),
            currency='EUR',
            reference='REF-1',
            sca_redirect_url='https://secret.example/sca',
            authorization_id='auth-secret',
            otp_payment_id='otp-secret',
            last_error='internal traceback should not leak',
        )
        cls.import_run = BankImportRun.all_objects.create(
            tenant=cls.tenant,
            actor=cls.viewer,
            content_sha256='a' * 64,
            status=BankImportRun.STATUS_SUCCEEDED,
            format='camt053',
            original_filename='x.xml',
            statements_created=1,
            transactions_created=1,
        )
        cls.sync_run = BankSyncRun.all_objects.create(
            tenant=cls.tenant,
            connection=cls.connection,
            actor=cls.viewer,
            status=BankSyncRun.STATUS_SUCCEEDED,
            transactions_created=3,
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
        response = client.get('/api/banking/overview/')
        self.assertEqual(response.status_code, 401)
        self.assertIn('Bearer', response.get('WWW-Authenticate', ''))

    def test_outsider_on_tenant_host_gets_404(self):
        client = self._auth_client(user=self.outsider, host=HOST)
        response = client.get('/api/banking/overview/')
        self.assertEqual(response.status_code, 404)

    def test_cross_tenant_statement_404(self):
        client = self._auth_client(host=OTHER_HOST)
        # viewer is not member of other — 404 via permission
        response = client.get(f'/api/banking/statements/{self.statement.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_overview_viewer_ok(self):
        client = self._auth_client()
        response = client.get('/api/banking/overview/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('accounts', data)
        self.assertEqual(data['unmatched_transaction_count'], 1)
        self.assertEqual(len(data['accounts']), 1)
        balances = data['accounts'][0]['balances']
        self.assertEqual(balances[0]['balance_type'], 'statement-closing')
        self.assertEqual(balances[0]['source'], 'statement')
        self.assertEqual(balances[0]['amount'], '1100.00')
        self.assertIn('is_stale', balances[0])

    def test_bank_accounts_list(self):
        client = self._auth_client()
        response = client.get('/api/banking/bank-accounts/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['iban'], IBAN)
        self.assertEqual(data['results'][0]['connection']['id'], self.connection.pk)

    def test_statements_filter_and_detail(self):
        client = self._auth_client()
        listed = client.get(
            '/api/banking/statements/',
            {'bank_account': self.account.pk, 'status': 'imported'},
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()['count'], 1)

        detail = client.get(f'/api/banking/statements/{self.statement.pk}/')
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertEqual(body['statement_number'], 'ST-100')
        self.assertEqual(body['match_summary'].get('unmatched'), 1)

        missing = client.get('/api/banking/statements/999999/')
        self.assertEqual(missing.status_code, 404)

    def test_transactions_filters(self):
        client = self._auth_client()
        response = client.get(
            '/api/banking/transactions/',
            {
                'bank_account': self.account.pk,
                'match_status': 'unmatched',
                'date_from': '2026-08-01',
                'date_to': '2026-08-01',
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['id'], self.tx.pk)

        bad = client.get('/api/banking/transactions/', {'match_status': 'ignored'})
        self.assertEqual(bad.status_code, 400)

    def test_payment_orders_allowlist_and_masked_iban(self):
        client = self._auth_client()
        response = client.get('/api/banking/payment-orders/')
        self.assertEqual(response.status_code, 200)
        row = response.json()['results'][0]
        self.assertEqual(set(row.keys()), set(PAYMENT_ORDER_ALLOWLIST))
        self.assertNotIn('sca_redirect_url', row)
        self.assertNotIn('authorization_id', row)
        self.assertNotIn('otp_payment_id', row)
        self.assertNotIn('last_error', row)
        self.assertEqual(row['debtor_iban'], mask_iban(IBAN))
        self.assertEqual(row['amount'], '42.50')

        dto = payment_order_dto(self.order)
        self.assertEqual(set(dto.keys()), set(PAYMENT_ORDER_ALLOWLIST))

    def test_statement_import_status(self):
        client = self._auth_client()
        response = client.get(f'/api/banking/statement-imports/{self.import_run.pk}/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], BankImportRun.STATUS_SUCCEEDED)
        self.assertEqual(data['transactions_created'], 1)
        self.assertNotIn('payload', data)
        self.assertNotIn('content_sha256', data)

        other_run = BankImportRun.all_objects.create(
            tenant=self.other,
            actor=self.outsider,
            content_sha256='b' * 64,
            status=BankImportRun.STATUS_QUEUED,
        )
        leak = client.get(f'/api/banking/statement-imports/{other_run.pk}/')
        self.assertEqual(leak.status_code, 404)

    def test_sync_status(self):
        client = self._auth_client()
        response = client.get(f'/api/banking/connections/{self.connection.pk}/sync-status/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['connection_id'], self.connection.pk)
        self.assertEqual(data['latest_sync']['id'], self.sync_run.pk)
        self.assertIsNone(data['active_sync'])

        missing = client.get('/api/banking/connections/999999/sync-status/')
        self.assertEqual(missing.status_code, 404)

    def test_write_methods_not_allowed(self):
        client = self._auth_client()
        for path in (
            '/api/banking/overview/',
            '/api/banking/bank-accounts/',
            '/api/banking/transactions/',
            '/api/banking/payment-orders/',
        ):
            response = client.post(path, {}, format='json')
            self.assertEqual(response.status_code, 405, path)

    def test_stale_balance_flag(self):
        stale_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Stari račun',
            bank_name='OTP',
            account_number='stale-1',
            iban='HR5324070001024070003',
            currency='EUR',
        )
        BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='ST-OLD',
            bank_account=stale_account,
            statement_date=timezone.localdate() - timedelta(days=30),
            opening_balance=Decimal('1.00'),
            closing_balance=Decimal('2.00'),
            imported_by=self.viewer,
        )
        client = self._auth_client()
        response = client.get('/api/banking/bank-accounts/')
        rows = {row['id']: row for row in response.json()['results']}
        balances = rows[stale_account.pk]['balances']
        self.assertTrue(balances[0]['is_stale'])
        self.assertEqual(balances[0]['amount'], '2.00')
