"""Slice 1: BankImportRun / BankSyncRun models, orchestration, audit command."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.test import TestCase, TransactionTestCase, override_settings

from banking.models import (
    BankImportRun,
    BankStatement,
    BankSyncRun,
    BankTransaction,
    bank_import_payload_upload_to,
    sanitize_original_filename,
)
from banking.provider_models import BankConnection, BankProvider
from banking.services.import_runs import (
    IdempotencyConflict,
    create_import_run,
    execute_import_run,
)
from banking.services.sync_runs import create_or_get_active_sync_run, execute_sync_run
from payments.models import BankAccount, Payment
from tenants.models import Tenant

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
SAMPLE_XML = (FIXTURES / 'otp_camt053_sample.xml').read_bytes()
KNOWN_IBAN = 'HR6124070001100204771'


class SanitizeFilenameTests(TestCase):
    def test_strips_path_and_unsafe_chars(self):
        self.assertEqual(
            sanitize_original_filename('../../evil name.xml'),
            'evil_name.xml',
        )

    def test_empty_falls_back(self):
        self.assertEqual(sanitize_original_filename(''), 'upload.bin')


class BankImportRunModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='importrun', password='test')
        self.tenant = Tenant.objects.create(slug='import-run-t', name='Import Run T')
        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Fine Star EUR',
            bank_name='OTP',
            account_number='0204771',
            iban=KNOWN_IBAN,
            currency='EUR',
        )

    def test_payload_upload_path_is_tenant_scoped(self):
        run = BankImportRun(
            tenant=self.tenant,
            actor=self.user,
            content_sha256='a' * 64,
            status=BankImportRun.STATUS_QUEUED,
        )
        path = bank_import_payload_upload_to(run, 'ignored.xml')
        self.assertTrue(path.startswith(f'banking/imports/{self.tenant.pk}/'))
        self.assertTrue(path.endswith('/payload.bin'))
        self.assertNotIn('ignored.xml', path)

    def test_create_camt_run_queued(self):
        outcome = create_import_run(
            tenant=self.tenant,
            actor=self.user,
            content=SAMPLE_XML,
            filename='sample.xml',
        )
        self.assertTrue(outcome.created)
        self.assertFalse(outcome.rejected)
        self.assertEqual(outcome.run.status, BankImportRun.STATUS_QUEUED)
        self.assertEqual(outcome.run.format, 'camt053')
        self.assertTrue(outcome.run.payload.name)
        self.assertEqual(len(outcome.run.content_sha256), 64)

    def test_reject_csv_without_enqueue(self):
        csv_body = b'datum;iznos;opis\n2026-01-01;100,00;Uplata'
        outcome = create_import_run(
            tenant=self.tenant,
            actor=self.user,
            content=csv_body,
            filename='x.csv',
        )
        self.assertTrue(outcome.rejected)
        self.assertEqual(outcome.run.status, BankImportRun.STATUS_REJECTED)
        self.assertEqual(outcome.run.format, 'csv')
        self.assertTrue(outcome.run.finished_at)

    def test_execute_camt_succeeds(self):
        outcome = create_import_run(
            tenant=self.tenant,
            actor=self.user,
            content=SAMPLE_XML,
            filename='sample.xml',
        )
        run = execute_import_run(outcome.run.pk)
        self.assertEqual(run.status, BankImportRun.STATUS_SUCCEEDED)
        self.assertEqual(run.statements_created, 6)
        self.assertEqual(run.transactions_created, 7)
        self.assertEqual(
            BankStatement.all_objects.filter(tenant=self.tenant).count(),
            6,
        )

    def test_idempotency_replay_same_hash(self):
        key = 'idem-1'
        first = create_import_run(
            tenant=self.tenant,
            actor=self.user,
            content=SAMPLE_XML,
            filename='a.xml',
            idempotency_key=key,
        )
        second = create_import_run(
            tenant=self.tenant,
            actor=self.user,
            content=SAMPLE_XML,
            filename='b.xml',
            idempotency_key=key,
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.run.pk, second.run.pk)

    def test_idempotency_conflict_different_hash(self):
        key = 'idem-2'
        create_import_run(
            tenant=self.tenant,
            actor=self.user,
            content=SAMPLE_XML,
            filename='a.xml',
            idempotency_key=key,
        )
        with self.assertRaises(IdempotencyConflict):
            create_import_run(
                tenant=self.tenant,
                actor=self.user,
                content=SAMPLE_XML + b'\n',
                filename='a.xml',
                idempotency_key=key,
            )

    @override_settings(BANKING_IMPORT_MAX_BYTES=10)
    def test_rejects_oversized_payload(self):
        with self.assertRaises(ValidationError):
            create_import_run(
                tenant=self.tenant,
                actor=self.user,
                content=b'01234567890',
                filename='big.bin',
            )


class BankSyncRunModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='syncrun', password='test')
        self.tenant = Tenant.objects.create(slug='sync-run-t', name='Sync Run T')
        provider = BankProvider.objects.create(
            code='otp-sync-run',
            name='OTP Sync',
            environment='sandbox',
            iam_base='https://iam.example.test',
            api_base='https://api.example.test',
            service_host='api.example.test',
        )
        account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni EUR',
            bank_name='OTP',
            account_number=KNOWN_IBAN,
            iban=KNOWN_IBAN,
        )
        self.connection = BankConnection.all_objects.create(
            tenant=self.tenant,
            bank_provider=provider,
            bank_account=account,
            status='connected',
        )

    def test_one_active_sync_per_connection(self):
        run1, created1 = create_or_get_active_sync_run(
            connection=self.connection,
            actor=self.user,
        )
        run2, created2 = create_or_get_active_sync_run(
            connection=self.connection,
            actor=self.user,
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(run1.pk, run2.pk)
        self.assertEqual(run1.status, BankSyncRun.STATUS_QUEUED)

    def test_unique_active_constraint_at_db(self):
        create_or_get_active_sync_run(connection=self.connection, actor=self.user)
        with self.assertRaises(IntegrityError):
            BankSyncRun.all_objects.create(
                tenant=self.tenant,
                connection=self.connection,
                actor=self.user,
                status=BankSyncRun.STATUS_QUEUED,
            )

    def test_execute_sync_run_success(self):
        run, _ = create_or_get_active_sync_run(
            connection=self.connection,
            actor=self.user,
            auto_create_payments=False,
        )
        with patch(
            'banking.services.sync_runs.sync_connection_transactions',
            return_value=3,
        ):
            finished = execute_sync_run(run.pk)
        self.assertEqual(finished.status, BankSyncRun.STATUS_SUCCEEDED)
        self.assertEqual(finished.transactions_created, 3)
        self.assertTrue(finished.finished_at)


class BankImportSyncMigrationTests(TestCase):
    def test_tables_and_constraints_exist(self):
        table_names = set(connection.introspection.table_names())
        self.assertIn('banking_bankimportrun', table_names)
        self.assertIn('banking_banksyncrun', table_names)

        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor,
                'banking_banksyncrun',
            )
        self.assertIn('unique_active_bank_sync_per_connection', constraints)


class AuditBankMatchDuplicatesTests(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='auditusr', password='test')
        self.tenant = Tenant.objects.create(slug='audit-match-t', name='Audit Match')
        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni',
            bank_name='OTP',
            account_number='1',
            iban=KNOWN_IBAN,
        )
        self.statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='AUD-1',
            bank_account=self.bank_account,
            statement_date=date(2026, 8, 1),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('100.00'),
            imported_by=self.user,
        )

    def test_clean_data_reports_success(self):
        out = StringIO()
        call_command('audit_bank_match_duplicates', tenant='audit-match-t', stdout=out)
        self.assertIn('Nema duplikata', out.getvalue())

    def test_detects_duplicate_matched_payment(self):
        from django.db import connection

        payment = Payment.all_objects.create(
            tenant=self.tenant,
            payment_number='PAY-AUD-1',
            payment_type='incoming',
            payment_method='bank_transfer',
            status='completed',
            amount=Decimal('50.00'),
            currency='EUR',
            bank_account=self.bank_account,
            payment_date=date(2026, 8, 1),
            counterparty_name='X',
            created_by=self.user,
        )
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX IF EXISTS unique_banktx_matched_payment')
        try:
            for i in range(2):
                BankTransaction.all_objects.create(
                    tenant=self.tenant,
                    bank_statement=self.statement,
                    transaction_date=date(2026, 8, 1),
                    amount=Decimal('50.00'),
                    transaction_type='credit',
                    description=f'tx-{i}',
                    external_id=f'ext-{i}',
                    match_status='matched',
                    matched_payment=payment,
                )
            out = StringIO()
            call_command('audit_bank_match_duplicates', tenant='audit-match-t', stdout=out)
            text = out.getvalue()
            self.assertIn('Duplikati matched_payment', text)
            self.assertIn(f'payment_id={payment.pk}', text)
        finally:
            BankTransaction.all_objects.filter(matched_payment=payment).update(
                matched_payment=None,
                match_status='unmatched',
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    'CREATE UNIQUE INDEX IF NOT EXISTS unique_banktx_matched_payment '
                    'ON banking_banktransaction (matched_payment_id) '
                    'WHERE matched_payment_id IS NOT NULL'
                )
