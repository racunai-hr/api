from __future__ import annotations

import time
import uuid
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase, override_settings

from banking.adapters.normalizer import normalize_transaction, to_bank_transactions
from banking.otp.oauth import OauthStateError, build_authorize_url, sign_oauth_state, verify_oauth_state
from banking.provider_models import BankProvider
from tenants.middleware import TenantMiddleware
from tenants.service_hosts import ServiceHostKind, resolve_service_host


@override_settings(
    SECRET_KEY='test-secret-key-for-otp-state',
    OTP_SERVICE_HOST='otp-sbx.racunai.hr',
)
class OauthStateTests(TestCase):
    def test_build_authorize_url_uses_connect_endpoint(self):
        url = build_authorize_url(
            iam_base='https://iam.sandbox.otpbanka.hr',
            client_id='PSD-SANDBOX-ID166',
            redirect_uri='https://otp-sbx.racunai.hr/oauth/callback/',
            state='test',
        )
        self.assertIn('/connect/authorize?', url)
        self.assertIn('grant_type=code', url)
        self.assertIn('scope=OTP.PSD2', url)
        self.assertIn('client_id=PSD-SANDBOX-ID166', url)

    def test_sign_and_verify_roundtrip(self):
        payload = {
            'tenant_slug': 'demo',
            'connection_id': 1,
            'nonce': str(uuid.uuid4()),
            'exp': time.time() + 3600,
        }
        state = sign_oauth_state(payload)
        verified = verify_oauth_state(state)
        self.assertEqual(verified['tenant_slug'], 'demo')
        self.assertEqual(verified['connection_id'], 1)

    def test_expired_state_rejected(self):
        state = sign_oauth_state({'exp': time.time() - 10})
        with self.assertRaises(OauthStateError):
            verify_oauth_state(state)


@override_settings(OTP_SERVICE_HOST='otp-sbx.racunai.hr')
class ServiceHostRegistryTests(TestCase):
    def test_otp_sbx_host_resolves(self):
        self.assertEqual(
            resolve_service_host('otp-sbx.racunai.hr'),
            ServiceHostKind.OTP,
        )

    def test_tenant_host_not_otp(self):
        self.assertIsNone(resolve_service_host('demo.racunai.hr'))


@override_settings(
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    OTP_SERVICE_HOST='otp-sbx.racunai.hr',
    ALLOWED_HOSTS=['otp-sbx.racunai.hr', 'demo.racunai.hr'],
)
class OtpMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = TenantMiddleware(lambda request: request)

    def test_otp_host_has_no_tenant(self):
        request = self.factory.get('/oauth/callback/', HTTP_HOST='otp-sbx.racunai.hr')
        response = self.middleware(request)
        self.assertIsNone(response.tenant)
        self.assertEqual(response.service_host_kind, ServiceHostKind.OTP)


class NormalizerTests(TestCase):
    def test_normalize_otp_transaction_fixture(self):
        raw = {
            'transactionId': 'tx-123',
            'bookingDate': '2026-01-15',
            'valueDate': '2026-01-15',
            'transactionAmount': {'amount': '100.50', 'currency': 'EUR'},
            'creditDebitIndicator': 'CRDT',
            'remittanceInformationUnstructured': 'Uplata računa',
            'endToEndId': 'REF-001',
            'creditorName': 'Kupac d.o.o.',
            'creditorAccount': {'iban': 'HR1234567890123456789'},
        }
        tx = normalize_transaction(raw)
        self.assertIsNotNone(tx)
        assert tx is not None
        self.assertEqual(tx.external_id, 'tx-123')
        self.assertEqual(tx.transaction_type, 'credit')
        self.assertEqual(len(to_bank_transactions([raw])), 1)


class MockConsentFlowTests(TestCase):
    @override_settings(
        OTP_CLIENT_ID='PSD-SANDBOX-ID166',
        OTP_REDIRECT_URI='https://otp-sbx.racunai.hr/oauth/callback/',
    )
    def test_start_connect_flow_returns_oauth_redirect(self):
        from tenants.models import Tenant
        from payments.models import BankAccount

        tenant = Tenant.objects.create(slug='banktest', name='Bank Test')
        provider, _ = BankProvider.objects.get_or_create(
            code='otp_sandbox',
            defaults={
                'name': 'OTP Sandbox',
                'environment': 'sandbox',
                'iam_base': 'https://iam.sandbox.otpbanka.hr',
                'api_base': 'https://api.sandbox.otpbanka.hr',
                'service_host': 'otp-sbx.racunai.hr',
            },
        )
        bank_account = BankAccount.all_objects.create(
            tenant=tenant,
            account_name='Test',
            bank_name='OTP',
            account_number='123',
            iban='HR0012345678901234567',
        )

        from banking.services.connect import start_connect_flow

        connection, url = start_connect_flow(tenant=tenant, bank_account=bank_account)
        self.assertEqual(connection.bank_provider, provider)
        self.assertEqual(connection.status, 'authorizing')
        self.assertIn('/connect/authorize', url)
        self.assertIn('client_id=PSD-SANDBOX-ID166', url)


class BankAccountDiscoveryTests(TestCase):
    def test_discover_accounts_from_api(self):
        from banking.provider_models import BankConnection
        from banking.services.discovery import discover_accounts_from_api
        from payments.models import BankAccount
        from tenants.models import Tenant

        tenant = Tenant.objects.create(slug='discover-test', name='Discover')
        provider = BankProvider.objects.create(
            code='otp_sandbox_disc',
            name='OTP',
            environment='sandbox',
            iam_base='https://iam.example',
            api_base='https://api.example',
            service_host='otp-sbx.racunai.hr',
        )
        account = BankAccount.all_objects.create(
            tenant=tenant,
            account_name='OTP račun',
            bank_name='OTP',
            account_number='pending-discover-test',
            iban=None,
            status='pending_discovery',
        )
        connection = BankConnection.all_objects.create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=account,
            status='connected',
        )
        accounts = [{
            'resourceId': 'res-001',
            'iban': 'HR6812345678901234567',
            'currency': 'EUR',
            'name': 'Glavni račun',
        }]
        discover_accounts_from_api(connection, accounts, primary_account=account)
        account.refresh_from_db()
        self.assertEqual(account.status, 'active')
        self.assertEqual(account.iban, 'HR6812345678901234567')
        self.assertEqual(account.external_account_id, 'res-001')
        self.assertIsNotNone(account.discovered_at)


class BankConnectionLifecycleTests(TestCase):
    def test_pis_ready_requires_sync_streak(self):
        from datetime import date

        from banking.provider_models import BankConnection, BankConsent
        from banking.tasks import check_consent_expiry_task
        from payments.models import BankAccount
        from tenants.models import Tenant

        tenant = Tenant.objects.create(slug='pis-gate', name='PIS Gate')
        provider = BankProvider.objects.create(
            code='otp_sandbox_pis',
            name='OTP',
            environment='sandbox',
            iam_base='https://iam.example',
            api_base='https://api.example',
            service_host='otp-sbx.racunai.hr',
        )
        account = BankAccount.all_objects.create(
            tenant=tenant,
            account_name='Test',
            bank_name='OTP',
            account_number='1',
            iban='HR001',
        )
        connection = BankConnection.all_objects.create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=account,
            status='connected',
            sync_success_streak=2,
        )
        self.assertFalse(connection.is_pis_ready())
        connection.sync_success_streak = 3
        connection.save()
        BankConsent.objects.create(
            connection=connection,
            status='authorized',
            valid_until=date(2099, 12, 31),
        )
        self.assertTrue(connection.is_pis_ready())


class ConsentExpiryTaskTests(TestCase):
    def test_expired_consent_updates_connection(self):
        from datetime import date

        from banking.provider_models import BankConnection, BankConsent
        from banking.tasks import check_consent_expiry_task
        from payments.models import BankAccount
        from tenants.models import Tenant

        tenant = Tenant.objects.create(slug='expiry-test', name='Expiry')
        provider = BankProvider.objects.create(
            code='otp_sandbox_exp',
            name='OTP',
            environment='sandbox',
            iam_base='https://iam.example',
            api_base='https://api.example',
            service_host='otp-sbx.racunai.hr',
        )
        account = BankAccount.all_objects.create(
            tenant=tenant,
            account_name='Test',
            bank_name='OTP',
            account_number='1',
            iban='HR001',
        )
        connection = BankConnection.all_objects.create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=account,
            status='connected',
        )
        consent = BankConsent.objects.create(
            connection=connection,
            status='authorized',
            valid_until=date(2020, 1, 1),
        )
        result = check_consent_expiry_task()
        consent.refresh_from_db()
        connection.refresh_from_db()
        self.assertEqual(consent.status, 'expired')
        self.assertEqual(connection.status, 'expired')
        self.assertIn(consent.pk, result['notified_consents'])


class StaleConnectionTaskTests(TestCase):
    def test_stale_connected_flagged(self):
        from datetime import timedelta

        from django.utils import timezone

        from banking.provider_models import BankConnection
        from banking.tasks import check_stale_connections_task
        from payments.models import BankAccount
        from tenants.models import Tenant

        tenant = Tenant.objects.create(slug='stale-test', name='Stale')
        provider = BankProvider.objects.create(
            code='otp_sandbox_stale',
            name='OTP',
            environment='sandbox',
            iam_base='https://iam.example',
            api_base='https://api.example',
            service_host='otp-sbx.racunai.hr',
        )
        account = BankAccount.all_objects.create(
            tenant=tenant,
            account_name='Test',
            bank_name='OTP',
            account_number='1',
            iban='HR001',
        )
        connection = BankConnection.all_objects.create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=account,
            status='connected',
            last_sync_at=timezone.now() - timedelta(hours=48),
        )
        result = check_stale_connections_task()
        self.assertIn(connection.pk, result['stale_connections'])


class ProvisionCommandTests(TestCase):
    def test_provision_dry_run(self):
        from io import StringIO

        from django.contrib.auth.models import User
        from django.core.management import call_command

        User.objects.create_user('otpadmin', password='test')
        out = StringIO()
        call_command(
            'provision_otp_test_tenants',
            '--dry-run',
            '--admin-user=otpadmin',
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn('otp-company-no1', output)
        self.assertIn('164.company.no1', output)
