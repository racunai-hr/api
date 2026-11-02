from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from accounting.models import JournalEntry
from accounting.services.chart import provision_tenant_chart
from banking.models import BankStatement, BankTransaction
from banking.services.hrk_statement_booking import (
    KIND_ATM,
    KIND_CAPITAL,
    KIND_FEE,
    KIND_INTEREST,
    KIND_LOAN_IN,
    KIND_LOAN_OUT,
    NIKOLA_NAME,
    NIKOLA_NOTE,
    HrkLineBookingError,
    book_hrk_statement_lines,
    classify_hrk_line,
)
from partners.models import Partner
from payments.models import BankAccount
from tenants.models import Tenant


def _line(**overrides):
    payload = {
        'transaction_date': date(2014, 3, 4),
        'amount': Decimal('1500.00'),
        'transaction_type': 'debit',
        'description': 'ATM GOTOVINA/03.03/HRK/1500,00',
        'counterparty_name': 'OTP banka d.d.',
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


class HrkLineClassificationTests(SimpleTestCase):
    def test_capital_loan_atm_fee_and_interest(self):
        capital = classify_hrk_line(_line(
            transaction_date=date(2013, 12, 20),
            amount=Decimal('20000.00'),
            transaction_type='credit',
            description='POVRAT OSN.POL.UPL.06/12/13',
        ))
        self.assertEqual(capital.kind, KIND_CAPITAL)
        self.assertEqual([leg.account_code for leg in capital.legs], ['002', '9000', 'BANK', '002'])

        loan = classify_hrk_line(_line(
            transaction_date=date(2014, 7, 18),
            amount=Decimal('500.00'),
            transaction_type='credit',
            description='POZAJMICA',
            counterparty_name='NIKOLA BANOVIĆ',
        ))
        self.assertEqual(loan.kind, KIND_LOAN_IN)

        repayment = classify_hrk_line(_line(
            transaction_date=date(2014, 7, 22),
            amount=Decimal('500.00'),
            description='ATM GOTOVINA/21.07/HRK/500,00',
        ))
        self.assertEqual(repayment.kind, KIND_LOAN_OUT)

        atm = classify_hrk_line(_line())
        self.assertEqual(atm.kind, KIND_ATM)
        self.assertIn(NIKOLA_NAME, atm.summary)

        fee = classify_hrk_line(_line(description='Naknade od 01.01.2014 do 31.01.2014'))
        self.assertEqual(fee.kind, KIND_FEE)

        interest = classify_hrk_line(_line(
            transaction_type='credit',
            description='PRIPIS KAMATE',
        ))
        self.assertEqual(interest.kind, KIND_INTEREST)

    def test_unknown_description_raises(self):
        with self.assertRaises(HrkLineBookingError):
            classify_hrk_line(_line(description='Virman dobavljaču'))


class HrkStatementBookingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)
        cls.tenant = Tenant.objects.create(slug='hrkbook', name='HRK Book')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='hrkbooker', password='test')
        cls.bank_account = BankAccount.all_objects.create(
            tenant=cls.tenant,
            account_name='Kuna',
            bank_name='OTP',
            account_number='1100396357',
            iban='HR5524070001100396357',
            currency='HRK',
        )

    def _statement(self, number: str, on: date) -> BankStatement:
        return BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number=number,
            bank_account=self.bank_account,
            statement_date=on,
            currency='HRK',
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('0.00'),
            imported_by=self.user,
        )

    def _tx(self, statement, **overrides) -> BankTransaction:
        payload = {
            'tenant': self.tenant,
            'bank_statement': statement,
            'transaction_date': statement.statement_date,
            'amount': Decimal('1500.00'),
            'currency': 'HRK',
            'amount_eur': Decimal('199.08'),
            'transaction_type': 'debit',
            'description': 'ATM GOTOVINA/03.03/HRK/1500,00',
            'counterparty_name': 'OTP banka d.d.',
        }
        payload.update(overrides)
        return BankTransaction.all_objects.create(**payload)

    def test_atm_books_matches_and_second_run_skips(self):
        statement = self._statement('2014-4', date(2014, 3, 4))
        bank_tx = self._tx(statement)

        first = book_hrk_statement_lines(self.tenant, self.user, self.bank_account)
        second = book_hrk_statement_lines(self.tenant, self.user, self.bank_account)

        bank_tx.refresh_from_db()
        statement.refresh_from_db()
        self.bank_account.refresh_from_db()
        entry = bank_tx.matched_journal_entry
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].startswith('booked '))
        self.assertEqual(second, [f'skip [hrk-izvod:btx-{bank_tx.pk}]'])
        self.assertEqual(bank_tx.match_status, 'matched')
        self.assertEqual(statement.status, 'reconciled')
        self.assertEqual(self.bank_account.ledger_account.account_code, '1000')
        self.assertEqual(JournalEntry.all_objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(entry.status, 'posted')
        self.assertEqual(entry.entry_date, date(2014, 3, 4))
        amounts = {
            (line.account.account_code, line.debit_amount, line.credit_amount)
            for line in entry.lines.select_related('account')
        }
        self.assertIn(('1331', Decimal('199.08'), Decimal('0')), amounts)
        self.assertIn(('1000', Decimal('0'), Decimal('199.08')), amounts)
        partner = Partner.all_objects.get(tenant=self.tenant, name=NIKOLA_NAME)
        self.assertEqual(partner.partner_type, 'other')
        self.assertEqual(partner.city, 'Zagreb')
        self.assertIn('1054106290', partner.notes)
        self.assertIn(NIKOLA_NOTE, partner.notes)

    def test_capital_payment_nets_unpaid_capital_to_zero(self):
        statement = self._statement('2013-1', date(2013, 12, 20))
        bank_tx = self._tx(
            statement,
            amount=Decimal('20000.00'),
            amount_eur=Decimal('2654.46'),
            transaction_type='credit',
            description='POVRAT OSN.POL.UPL.06/12/13',
            reference='6824-061213-201213',
        )
        book_hrk_statement_lines(self.tenant, self.user, self.bank_account)
        bank_tx.refresh_from_db()
        entry = bank_tx.matched_journal_entry
        self.assertEqual(entry.lines.count(), 4)
        self.assertEqual(entry.reference, '6824-061213-201213')
        net: dict[str, Decimal] = {}
        bank_debits = 0
        for line in entry.lines.select_related('account'):
            net[line.account.account_code] = (
                net.get(line.account.account_code, Decimal('0'))
                + line.debit_amount
                - line.credit_amount
            )
            if line.account.account_code == '1000' and line.debit_amount == Decimal('2654.46'):
                bank_debits += 1
        self.assertEqual(net['002'], Decimal('0'))
        self.assertEqual(net['9000'], Decimal('-2654.46'))
        self.assertEqual(net['1000'], Decimal('2654.46'))
        self.assertEqual(bank_debits, 1)

    def test_loan_repayment_credits_the_bank_from_2145(self):
        statement = self._statement('2014-24', date(2014, 7, 22))
        bank_tx = self._tx(
            statement,
            amount=Decimal('500.00'),
            amount_eur=Decimal('66.36'),
            description='ATM GOTOVINA/21.07/HRK/500,00',
        )
        book_hrk_statement_lines(self.tenant, self.user, self.bank_account)
        bank_tx.refresh_from_db()
        codes = {line.account.account_code for line in bank_tx.matched_journal_entry.lines.all()}
        self.assertEqual(codes, {'2145', '1000'})

    def test_one_lipa_with_zero_eur_books_one_cent(self):
        statement = self._statement('2014-26', date(2014, 9, 30))
        bank_tx = self._tx(
            statement,
            amount=Decimal('0.01'),
            amount_eur=Decimal('0.00'),
            transaction_type='credit',
            description='PRIPIS KAMATE',
        )
        book_hrk_statement_lines(self.tenant, self.user, self.bank_account)
        bank_tx.refresh_from_db()
        self.assertEqual(bank_tx.amount_eur, Decimal('0.01'))
        debit = bank_tx.matched_journal_entry.lines.get(account__account_code='1000')
        self.assertEqual(debit.debit_amount, Decimal('0.01'))
        statement.refresh_from_db()
        self.assertEqual(statement.status, 'reconciled')

    def test_unknown_line_books_nothing(self):
        statement = self._statement('2014-x', date(2014, 1, 2))
        self._tx(statement, description='Virman')
        with self.assertRaises(HrkLineBookingError):
            book_hrk_statement_lines(self.tenant, self.user, self.bank_account)
        self.assertEqual(JournalEntry.all_objects.filter(tenant=self.tenant).count(), 0)
        self.assertFalse(Partner.all_objects.filter(tenant=self.tenant, name=NIKOLA_NAME).exists())
        self.bank_account.refresh_from_db()
        self.assertIsNone(self.bank_account.ledger_account_id)
