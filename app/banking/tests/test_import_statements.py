from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase

from banking.models import BankStatement, BankTransaction
from banking.parsers.camt053 import parse_camt053
from banking.parsers.format_detection import detect_format
from banking.services.import_statements import import_bank_statement_file
from payments.models import BankAccount
from tenants.models import Tenant

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
SAMPLE_XML = (FIXTURES / 'otp_camt053_sample.xml').read_bytes()
KNOWN_IBAN = 'HR6124070001100204771'
UNKNOWN_IBAN = 'HR0012345678901234567'


class Camt053ParserTests(TestCase):
    def test_parse_camt053_returns_six_statements(self):
        statements = parse_camt053(SAMPLE_XML)
        self.assertEqual(len(statements), 6)

    def test_parse_camt053_first_statement_balances(self):
        first = parse_camt053(SAMPLE_XML)[0]
        self.assertEqual(first.statement_number, '120741558')
        self.assertEqual(first.iban, KNOWN_IBAN)
        self.assertEqual(first.currency, 'EUR')
        self.assertEqual(first.opening_balance, Decimal('1061.31'))
        self.assertEqual(first.closing_balance, Decimal('1050.45'))
        self.assertEqual(len(first.transactions), 1)

    def test_parse_camt053_otp_reference_and_description(self):
        tx = parse_camt053(SAMPLE_XML)[0].transactions[0]
        self.assertEqual(tx.external_id, 'IPR 473052900')
        self.assertFalse(tx.used_fallback_external_id)
        self.assertEqual(tx.reference, 'HR17500060151018-6844')
        self.assertIn('Naknade od 01.12.2025', tx.description)
        self.assertEqual(tx.transaction_type, 'debit')
        self.assertEqual(tx.counterparty_name, 'OTP banka d.d.')


class FormatDetectionTests(TestCase):
    def test_detect_camt053_without_extension(self):
        self.assertEqual(detect_format(SAMPLE_XML, filename='upload'), 'camt053')

    def test_detect_mt940_markers(self):
        content = ':20:START\n:25:HR123\n:61:tx'
        self.assertEqual(detect_format(content), 'mt940')

    def test_detect_csv_header(self):
        content = 'datum;iznos;opis\n2026-01-01;100,00;Uplata'
        self.assertEqual(detect_format(content), 'csv')


class BankStatementImportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='importtest', password='test')
        self.tenant, _ = Tenant.objects.get_or_create(
            slug='bank-import-test',
            defaults={'name': 'Bank Import Test'},
        )
        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Fine Star EUR',
            bank_name='OTP',
            account_number='0204771',
            iban=KNOWN_IBAN,
            currency='EUR',
        )

    def _import(self, content=SAMPLE_XML, filename='sample.xml'):
        return import_bank_statement_file(
            tenant=self.tenant,
            user=self.user,
            content=content,
            filename=filename,
        )

    def test_import_bank_statement_file(self):
        result = self._import()
        self.assertTrue(result.import_id)
        self.assertEqual(result.format, 'camt053')
        self.assertEqual(result.statements_processed, 6)
        self.assertEqual(result.statements_created, 6)
        self.assertEqual(result.transactions_created, 7)
        self.assertEqual(
            BankStatement.all_objects.filter(tenant=self.tenant).count(),
            6,
        )
        self.assertEqual(
            BankTransaction.all_objects.filter(tenant=self.tenant).count(),
            7,
        )

    def test_reimport_skips_transactions_and_warns_on_balances(self):
        first = self._import()
        self.assertEqual(first.transactions_created, 7)

        second = self._import()
        self.assertEqual(second.transactions_created, 0)
        self.assertEqual(second.transactions_skipped, 7)
        self.assertEqual(second.statements_created, 0)
        self.assertEqual(second.statements_updated, 6)

    def test_unknown_iban_errors_single_statement(self):
        content = self._build_multi_iban_xml([
            (KNOWN_IBAN, '120741558'),
            (UNKNOWN_IBAN, '999999999'),
        ])
        result = self._import(content=content)
        self.assertEqual(result.statements_created, 1)
        self.assertEqual(result.transactions_created, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn('999999999', result.errors[0])
        self.assertIn(UNKNOWN_IBAN, result.errors[0])

    def test_multi_iban_maps_each_account(self):
        second_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Secondary EUR',
            bank_name='OTP',
            account_number='9999999',
            iban='HR5324070001024070003',
            currency='EUR',
        )
        content = self._build_multi_iban_xml([
            (KNOWN_IBAN, '120741558'),
            (second_account.iban, '121334037'),
        ])
        result = self._import(content=content)
        self.assertEqual(result.statements_created, 2)
        self.assertEqual(result.errors, ())

        stmt_a = BankStatement.all_objects.get(
            tenant=self.tenant,
            bank_account=self.bank_account,
            statement_number='120741558',
        )
        stmt_b = BankStatement.all_objects.get(
            tenant=self.tenant,
            bank_account=second_account,
            statement_number='121334037',
        )
        self.assertEqual(stmt_a.transactions.count(), 1)
        self.assertEqual(stmt_b.transactions.count(), 1)

    def test_partial_failure_keeps_first_statement(self):
        original = parse_camt053(SAMPLE_XML)
        failing_number = original[1].statement_number
        original_get_or_create = BankStatement.all_objects.get_or_create

        def flaky_get_or_create(*args, **kwargs):
            if kwargs.get('statement_number') == failing_number:
                raise RuntimeError('simulated DB failure')
            return original_get_or_create(*args, **kwargs)

        with patch.object(BankStatement.all_objects, 'get_or_create', side_effect=flaky_get_or_create):
            result = self._import()

        self.assertEqual(result.statements_created, 5)
        self.assertEqual(len(result.errors), 1)
        self.assertIn(failing_number, result.errors[0])
        self.assertTrue(
            BankStatement.all_objects.filter(
                tenant=self.tenant,
                statement_number=original[0].statement_number,
            ).exists(),
        )

    @staticmethod
    def _build_multi_iban_xml(pairs: list[tuple[str, str]]) -> bytes:
        text = SAMPLE_XML.decode('utf-8')
        blocks = re.findall(r'<Stmt>.*?</Stmt>', text, flags=re.DOTALL)
        if len(blocks) < len(pairs):
            raise ValueError('Fixture nema dovoljno Stmt elemenata')

        rebuilt = []
        for index, (iban, statement_number) in enumerate(pairs):
            block = blocks[index]
            block = re.sub(r'<Id>.*?</Id>', f'<Id>{statement_number}</Id>', block, count=1)
            block = re.sub(
                r'<IBAN>.*?</IBAN>',
                f'<IBAN>{iban}</IBAN>',
                block,
                count=1,
            )
            rebuilt.append(block)

        body = '\n        '.join(rebuilt)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">\n'
            '    <BkToCstmrStmt>\n'
            '        <GrpHdr><MsgId>TEST</MsgId></GrpHdr>\n'
            f'        {body}\n'
            '    </BkToCstmrStmt>\n'
            '</Document>\n'
        ).encode('utf-8')


class BankStatementParallelImportTests(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='parallel-import', password='test')
        self.tenant, _ = Tenant.objects.get_or_create(
            slug='bank-parallel-import',
            defaults={'name': 'Bank Parallel Import'},
        )
        BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Fine Star EUR',
            bank_name='OTP',
            account_number='0204771-parallel',
            iban=KNOWN_IBAN,
            currency='EUR',
        )

    def test_parallel_import_is_idempotent(self):
        def run_import():
            return import_bank_statement_file(
                tenant=self.tenant,
                user=self.user,
                content=SAMPLE_XML,
                filename='parallel.xml',
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run_import(), range(2)))

        self.assertEqual(
            BankStatement.all_objects.filter(tenant=self.tenant).count(),
            6,
        )
        self.assertEqual(
            BankTransaction.all_objects.filter(tenant=self.tenant).count(),
            7,
        )
        total_created = sum(r.transactions_created for r in results)
        total_skipped = sum(r.transactions_skipped for r in results)
        self.assertEqual(total_created + total_skipped, 14)
