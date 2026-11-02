from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from banking.importers.camt053_importer import Camt053Importer
from banking.models import BankTransaction
from banking.parsers.format_detection import detect_format
from banking.parsers.otp_izv import OtpWord, parse_otp_izv_words
from banking.services.import_statements import import_bank_statement_file
from payments.models import BankAccount
from tenants.models import Tenant

KNOWN_IBAN = 'HR6124070001100204771'
UNKNOWN_IBAN = 'HR9999999999999999999'
DE_IBAN = 'DE07100110012626702555'
OTP_IBAN = 'HR5324070001024070003'


def _line(y: float, *pairs: tuple[float, str]) -> list[OtpWord]:
    return [OtpWord(x, y, text) for x, text in pairs]


def _eur_page(
    *,
    seq: str,
    day: str,
    opening_credit: str,
    credit: str,
    debit: str,
    closing_credit: str,
    urb_credit: str,
    urb_debit: str,
    iban: str = KNOWN_IBAN,
) -> list[OtpWord]:
    words: list[OtpWord] = []
    words += _line(220, (300, 'Izvod'), (340, 'broj:'), (370, seq))
    words += _line(210, (300, 'prometu'), (340, 'dan:'), (400, day))
    words += _line(190, (200, 'Opis'), (500, 'Duguje'), (620, 'Potrazuje'))
    words += _line(
        170,
        (10, '1100204771'),
        (40, 'EUR'),
        (80, iban),
        (200, 'Pocetno'),
        (250, 'stanje:'),
        (510, '0,00'),
        (630, opening_credit),
    )
    words += _line(160, (20, DE_IBAN), (200, 'loan'))
    words += _line(150, (10, '1.'), (40, day), (80, 'Ante'), (120, 'Vrcan'))
    words += _line(
        140,
        (200, 'URB'),
        (230, urb_credit),
        (300, '210226232811'),
        (510, '0,00'),
        (630, credit),
    )
    words += _line(130, (20, OTP_IBAN), (200, 'KOMPENZ.KN/DUGOVANJA'))
    words += _line(120, (10, '2.'), (40, day), (80, 'OTP'), (110, 'banka'), (150, 'd.d.'))
    words += _line(
        110,
        (200, 'URB'),
        (230, urb_debit),
        (300, '210226232813'),
        (510, debit),
        (630, '0,00'),
    )
    words += _line(100, (200, 'Novo'), (250, 'stanje:'), (510, '0,00'), (630, closing_credit))
    words += _line(
        80,
        (10, '512340202067'),
        (50, 'HRK'),
        (200, 'Pocetno'),
        (250, 'stanje:'),
        (510, '0,00'),
        (630, '0,00'),
    )
    words += _line(70, (200, 'Novo'), (250, 'stanje:'), (510, '0,00'), (630, '0,00'))
    return words


def sample_pages() -> list[list[OtpWord]]:
    return [
        _eur_page(
            seq='2',
            day='26.02.2021',
            opening_credit='9.999,88',
            credit='13.000,00',
            debit='0,12',
            closing_credit='22.999,76',
            urb_credit='2351935893',
            urb_debit='2351935897',
        ),
        _eur_page(
            seq='1',
            day='25.02.2021',
            opening_credit='0,00',
            credit='10.000,00',
            debit='0,12',
            closing_credit='9.999,88',
            urb_credit='2350611200',
            urb_debit='2350611204',
        ),
    ]


class OtpIzvParserTests(TestCase):
    def test_two_eur_statements_skip_hrk_block(self):
        parsed = parse_otp_izv_words(sample_pages())
        self.assertEqual(
            [item.statement_number for item in parsed.statements],
            ['2021-2', '2021-1'],
        )
        self.assertEqual(len(parsed.warnings), 2)
        self.assertTrue(all('bez IBAN-a' in warning for warning in parsed.warnings))
        self.assertTrue(all('512340202067' in warning for warning in parsed.warnings))

        later = parsed.statements[0]
        self.assertEqual(later.iban, KNOWN_IBAN)
        self.assertEqual(later.currency, 'EUR')
        self.assertEqual(later.statement_date.isoformat(), '2021-02-26')
        self.assertEqual(later.opening_balance, Decimal('9999.88'))
        self.assertEqual(later.closing_balance, Decimal('22999.76'))
        self.assertEqual(len(later.transactions), 2)

        credit, debit = later.transactions
        self.assertEqual(credit.transaction_type, 'credit')
        self.assertEqual(credit.amount, Decimal('13000.00'))
        self.assertEqual(credit.external_id, '2351935893')
        self.assertEqual(credit.reference, '210226232811')
        self.assertEqual(credit.counterparty_name, 'Ante Vrcan')
        self.assertEqual(credit.counterparty_iban, DE_IBAN)
        self.assertEqual(credit.description, 'loan')
        self.assertFalse(credit.used_fallback_external_id)

        self.assertEqual(debit.transaction_type, 'debit')
        self.assertEqual(debit.amount, Decimal('0.12'))
        self.assertEqual(debit.external_id, '2351935897')
        self.assertEqual(debit.counterparty_name, 'OTP banka d.d.')
        self.assertEqual(debit.counterparty_iban, OTP_IBAN)
        self.assertEqual(debit.description, 'KOMPENZ.KN/DUGOVANJA')

        earlier = parsed.statements[1]
        self.assertEqual(earlier.opening_balance, Decimal('0.00'))
        self.assertEqual(earlier.closing_balance, Decimal('9999.88'))
        self.assertEqual(earlier.transactions[0].amount, Decimal('10000.00'))
        self.assertEqual(earlier.transactions[0].external_id, '2350611200')

    def test_detect_otp_pdf_and_reject_other_pdf(self):
        self.assertEqual(detect_format(b'%PDF-1.4 garbage', filename='izvod.pdf'), 'unknown')
        with patch('banking.parsers.otp_izv.pdf_text', return_value='Obrazac: IZV\nIzvod broj: 1'):
            self.assertEqual(detect_format(b'%PDF-1.4\n', filename='izvod.pdf'), 'otp_izv')


class OtpIzvImportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='otp-izv', password='test')
        self.tenant, _ = Tenant.objects.get_or_create(
            slug='otp-izv-import',
            defaults={'name': 'OTP IZV Import'},
        )
        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Fine Star EUR',
            bank_name='OTP',
            account_number='izv-0204771',
            iban=KNOWN_IBAN,
            currency='EUR',
        )

    def test_reimport_skips_same_urb(self):
        statements = parse_otp_izv_words(sample_pages()).statements
        importer = Camt053Importer()
        first = importer.import_parsed(
            tenant=self.tenant,
            user=self.user,
            statements=statements,
            import_id='first',
        )
        second = importer.import_parsed(
            tenant=self.tenant,
            user=self.user,
            statements=statements,
            import_id='second',
        )
        self.assertEqual(first.statements_created, 2)
        self.assertEqual(first.transactions_created, 4)
        self.assertEqual(second.transactions_created, 0)
        self.assertEqual(second.transactions_skipped, 4)
        self.assertEqual(
            BankTransaction.all_objects.filter(tenant=self.tenant).count(),
            4,
        )

    def test_unknown_iban_is_an_error(self):
        pages = [
            _eur_page(
                seq='3',
                day='27.02.2021',
                opening_credit='0,00',
                credit='1,00',
                debit='0,12',
                closing_credit='0,88',
                urb_credit='3000000001',
                urb_debit='3000000002',
                iban=UNKNOWN_IBAN,
            ),
        ]
        statements = parse_otp_izv_words(pages).statements
        result = Camt053Importer().import_parsed(
            tenant=self.tenant,
            user=self.user,
            statements=statements,
            import_id='unknown',
        )
        self.assertEqual(result.statements_created, 0)
        self.assertEqual(len(result.errors), 1)
        self.assertIn(UNKNOWN_IBAN, result.errors[0])

    def test_import_file_keeps_skip_warning(self):
        parsed = parse_otp_izv_words(sample_pages())
        with (
            patch('banking.services.import_statements.detect_format', return_value='otp_izv'),
            patch('banking.services.import_statements.parse_otp_izv', return_value=parsed),
        ):
            result = import_bank_statement_file(
                tenant=self.tenant,
                user=self.user,
                content=b'%PDF-1.4',
                filename='izvod.pdf',
            )
        self.assertEqual(result.format, 'otp_izv')
        self.assertEqual(result.statements_created, 2)
        self.assertEqual(result.transactions_created, 4)
        self.assertEqual(len(result.warnings), 2)
        self.assertTrue(all('bez IBAN-a' in warning for warning in result.warnings))
