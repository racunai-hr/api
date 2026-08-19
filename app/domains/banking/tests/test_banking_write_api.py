"""Acceptance tests for ADR-0021 banking JWT write API (Slice 4)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.services.chart import provision_tenant_chart
from accounting.services.manual_journal import JournalLineInput, create_manual_journal_entry
from accounting.services.posting import resolve_account
from banking.models import BankImportRun, BankStatement, BankSyncRun, BankTransaction
from banking.provider_models import BankConnection, BankProvider
from banking.services.import_runs import execute_import_run
from payments.models import BankAccount, Payment
from tenants.models import Tenant, TenantMembership

HOST = 'bankwrite.racunai.hr'
OTHER_HOST = 'bankwrite2.racunai.hr'
IBAN = 'HR6124070001100204771'
FIXTURES = Path(__file__).resolve().parents[3] / 'banking' / 'tests' / 'fixtures'
SAMPLE_XML = (FIXTURES / 'otp_camt053_sample.xml').read_bytes()


def _eager_import_enqueue(run_id: int):
    return execute_import_run(run_id)


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class BankingWriteApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)

        cls.tenant = Tenant.objects.create(slug='bankwrite', name='Bank Write Co')
        cls.other = Tenant.objects.create(slug='bankwrite2', name='Other Write Co')
        provision_tenant_chart(cls.tenant)

        User = get_user_model()
        cls.owner = User.objects.create_user(username='bw-owner', password='test')
        cls.accountant = User.objects.create_user(username='bw-acct', password='test')
        cls.viewer = User.objects.create_user(username='bw-viewer', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')

        cls.account = BankAccount.all_objects.create(
            tenant=cls.tenant,
            account_name='Fine Star EUR',
            bank_name='OTP',
            account_number='0204771',
            iban=IBAN,
            currency='EUR',
            ledger_account=resolve_account(cls.tenant, '1000'),
        )
        provider = BankProvider.objects.create(
            code='otp-bankwrite',
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
        )
        cls.statement = BankStatement.all_objects.create(
            tenant=cls.tenant,
            statement_number='BW-1',
            bank_account=cls.account,
            statement_date=date(2026, 8, 1),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('100.00'),
            imported_by=cls.owner,
        )

    def _client(self, user, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def _fresh_tx(self, suffix: str) -> BankTransaction:
        return BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 1),
            amount=Decimal('100.00'),
            transaction_type='credit',
            description=f'Uplata {suffix}',
            currency='EUR',
            external_id=f'bw-tx-{suffix}',
            match_status='unmatched',
        )

    def _fresh_payment(self, suffix: str, *, amount=Decimal('100.00')) -> Payment:
        return Payment.all_objects.create(
            tenant=self.tenant,
            payment_number=f'PAY-BW-{suffix}',
            payment_type='incoming',
            payment_method='bank_transfer',
            status='completed',
            amount=amount,
            currency='EUR',
            bank_account=self.account,
            payment_date=date(2026, 8, 1),
            created_by=self.owner,
        )

    def test_viewer_write_returns_404(self):
        client = self._client(self.viewer)
        tx = self._fresh_tx('viewer')
        payment = self._fresh_payment('viewer')
        response = client.post(
            '/api/banking/statement-imports/',
            {'file': SimpleUploadedFile('x.xml', SAMPLE_XML)},
            format='multipart',
            HTTP_IDEMPOTENCY_KEY='viewer-1',
        )
        self.assertEqual(response.status_code, 404)

        response = client.post(
            f'/api/banking/transactions/{tx.pk}/match/',
            {'target_type': 'payment', 'target_id': payment.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 404)

        response = client.post(f'/api/banking/connections/{self.connection.pk}/sync/')
        self.assertEqual(response.status_code, 404)

    @patch('domains.banking.write.service.enqueue_import_run', side_effect=_eager_import_enqueue)
    def test_camt_import_202_and_poll(self, _mock):
        client = self._client(self.accountant)
        response = client.post(
            '/api/banking/statement-imports/',
            {'file': SimpleUploadedFile('otp.xml', SAMPLE_XML, content_type='application/xml')},
            format='multipart',
            HTTP_IDEMPOTENCY_KEY='camt-1',
        )
        self.assertEqual(response.status_code, 202)
        run_id = response.json()['id']
        self.assertEqual(response.json()['status'], BankImportRun.STATUS_SUCCEEDED)

        poll = client.get(f'/api/banking/statement-imports/{run_id}/')
        self.assertEqual(poll.status_code, 200)
        self.assertGreaterEqual(poll.json()['transactions_created'], 1)

    def test_csv_import_422_creates_rejected_run(self):
        client = self._client(self.owner)
        csv_body = b'datum;iznos;opis\n2026-01-01;100,00;Uplata'
        response = client.post(
            '/api/banking/statement-imports/',
            {'file': SimpleUploadedFile('x.csv', csv_body, content_type='text/csv')},
            format='multipart',
            HTTP_IDEMPOTENCY_KEY='csv-1',
        )
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertEqual(data['status'], BankImportRun.STATUS_REJECTED)
        self.assertTrue(BankImportRun.all_objects.filter(pk=data['id'], status='rejected').exists())

    def test_idempotency_conflict_409(self):
        client = self._client(self.owner)
        client.post(
            '/api/banking/statement-imports/',
            {'file': SimpleUploadedFile('a.xml', SAMPLE_XML)},
            format='multipart',
            HTTP_IDEMPOTENCY_KEY='idem-conflict',
        )
        response = client.post(
            '/api/banking/statement-imports/',
            {'file': SimpleUploadedFile('a.xml', SAMPLE_XML + b'\n')},
            format='multipart',
            HTTP_IDEMPOTENCY_KEY='idem-conflict',
        )
        self.assertEqual(response.status_code, 409)

    def test_missing_idempotency_key_422(self):
        client = self._client(self.owner)
        response = client.post(
            '/api/banking/statement-imports/',
            {'file': SimpleUploadedFile('a.xml', SAMPLE_XML)},
            format='multipart',
        )
        self.assertEqual(response.status_code, 422)

    def test_match_unmatch_payment(self):
        tx = self._fresh_tx('mu')
        payment = self._fresh_payment('mu')
        client = self._client(self.accountant)
        match = client.post(
            f'/api/banking/transactions/{tx.pk}/match/',
            {'target_type': 'payment', 'target_id': payment.pk},
            format='json',
        )
        self.assertEqual(match.status_code, 200)
        self.assertEqual(match.json()['match_status'], 'matched')
        self.assertEqual(match.json()['matched_payment_id'], payment.pk)

        unmatch = client.post(f'/api/banking/transactions/{tx.pk}/unmatch/')
        self.assertEqual(unmatch.status_code, 200)
        self.assertEqual(unmatch.json()['match_status'], 'unmatched')
        self.assertIsNone(unmatch.json()['matched_payment_id'])
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'completed')

    def test_match_wrong_amount_422(self):
        tx = self._fresh_tx('amt')
        bad = self._fresh_payment('amt-bad', amount=Decimal('50.00'))
        client = self._client(self.owner)
        response = client.post(
            f'/api/banking/transactions/{tx.pk}/match/',
            {'target_type': 'payment', 'target_id': bad.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 422)

    def test_match_taken_target_409(self):
        tx_a = self._fresh_tx('taken-a')
        tx_b = self._fresh_tx('taken-b')
        payment = self._fresh_payment('taken')
        client = self._client(self.owner)
        first = client.post(
            f'/api/banking/transactions/{tx_a.pk}/match/',
            {'target_type': 'payment', 'target_id': payment.pk},
            format='json',
        )
        self.assertEqual(first.status_code, 200)
        second = client.post(
            f'/api/banking/transactions/{tx_b.pk}/match/',
            {'target_type': 'payment', 'target_id': payment.pk},
            format='json',
        )
        self.assertEqual(second.status_code, 409)

    def test_match_cross_tenant_target_404(self):
        tx = self._fresh_tx('xt')
        other_account = BankAccount.all_objects.create(
            tenant=self.other,
            account_name='Other',
            bank_name='OTP',
            account_number='x',
            iban='HR0012345678901234567',
        )
        foreign_payment = Payment.all_objects.create(
            tenant=self.other,
            payment_number='PAY-OTHER',
            payment_type='incoming',
            payment_method='bank_transfer',
            status='completed',
            amount=Decimal('100.00'),
            currency='EUR',
            bank_account=other_account,
            payment_date=date(2026, 8, 1),
            created_by=self.owner,
        )
        client = self._client(self.owner)
        response = client.post(
            f'/api/banking/transactions/{tx.pk}/match/',
            {'target_type': 'payment', 'target_id': foreign_payment.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 404)

    def test_match_journal_entry(self):
        tx = self._fresh_tx('je')
        entry = create_manual_journal_entry(
            self.tenant,
            self.owner,
            entry_date=date(2026, 8, 1),
            description='JE match',
            lines=[
                JournalLineInput('1000', Decimal('100.00'), Decimal('0')),
                JournalLineInput('7792', Decimal('0'), Decimal('100.00')),
            ],
            post=True,
        )
        client = self._client(self.owner)
        response = client.post(
            f'/api/banking/transactions/{tx.pk}/match/',
            {'target_type': 'journal_entry', 'target_id': entry.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['matched_journal_entry_id'], entry.pk)

    def test_sync_202_reuses_active_run(self):
        client = self._client(self.owner)

        def keep_queued(run_id):
            BankSyncRun.all_objects.filter(pk=run_id).update(celery_task_id='task-1')
            return BankSyncRun.all_objects.get(pk=run_id)

        with patch('domains.banking.write.service.enqueue_sync_run', side_effect=keep_queued):
            first = client.post(f'/api/banking/connections/{self.connection.pk}/sync/')
            self.assertEqual(first.status_code, 202)
            self.assertTrue(first.json()['created'])
            self.assertFalse(first.json()['auto_create_payments'])
            run_id = first.json()['id']

            second = client.post(f'/api/banking/connections/{self.connection.pk}/sync/')
            self.assertEqual(second.status_code, 202)
            self.assertFalse(second.json()['created'])
            self.assertEqual(second.json()['id'], run_id)

    def test_sync_unknown_connection_404(self):
        client = self._client(self.owner)
        response = client.post('/api/banking/connections/999999/sync/')
        self.assertEqual(response.status_code, 404)
