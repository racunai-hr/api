from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import JournalEntry
from accounting.services.chart import provision_tenant_chart
from accounting.services.manual_journal import JournalLineInput, create_manual_journal_entry
from accounting.services.posting import resolve_account
from banking.models import BankStatement, BankTransaction
from banking.reconciliation import (
    match_transaction_to_journal_entry,
    suggest_journal_matches,
    unmatch_transaction,
)
from payments.models import BankAccount
from tenants.models import Tenant


class ManualJournalBankMatchingTests(TestCase):
    def setUp(self):
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)

        self.tenant = Tenant.objects.create(slug='matchco', name='Match Co')
        provision_tenant_chart(self.tenant)
        User = get_user_model()
        self.user = User.objects.create_user(username='matchuser', password='test')

        self.ledger_1000 = resolve_account(self.tenant, '1000')
        self.account_2145 = resolve_account(self.tenant, '2145')
        self.account_7792 = resolve_account(self.tenant, '7792')

        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni EUR',
            bank_name='OTP',
            account_number='HR6124070001100204771',
            iban='HR6124070001100204771',
            ledger_account=self.ledger_1000,
        )
        self.statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='ST-001',
            bank_account=self.bank_account,
            statement_date=date(2026, 6, 18),
            opening_balance=Decimal('5000.00'),
            closing_balance=Decimal('3020.00'),
            imported_by=self.user,
        )
        self.bank_tx = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 6, 18),
            amount=Decimal('1980.00'),
            transaction_type='debit',
            description='Povrat pozajmice',
            counterparty_name='TONI SUPE',
        )

    def _create_split_entry(self) -> JournalEntry:
        return create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=date(2026, 6, 18),
            description='Povrat pozajmice — bruto 2000',
            lines=[
                JournalLineInput('2145', Decimal('2000.00'), Decimal('0')),
                JournalLineInput('1000', Decimal('0'), Decimal('1980.00')),
                JournalLineInput('7792', Decimal('0'), Decimal('20.00')),
            ],
            post=True,
        )

    def test_create_manual_journal_entry_split(self):
        entry = self._create_split_entry()
        self.assertEqual(entry.status, 'posted')
        self.assertEqual(entry.total_debit, Decimal('2000.00'))
        self.assertEqual(entry.total_credit, Decimal('2000.00'))

    def test_match_transaction_to_journal_entry(self):
        entry = self._create_split_entry()
        match_transaction_to_journal_entry(self.bank_tx, entry, self.user)
        self.bank_tx.refresh_from_db()
        self.assertEqual(self.bank_tx.matched_journal_entry_id, entry.id)
        self.assertEqual(self.bank_tx.match_status, 'matched')

    def test_match_is_idempotent(self):
        entry = self._create_split_entry()
        match_transaction_to_journal_entry(self.bank_tx, entry, self.user)
        match_transaction_to_journal_entry(self.bank_tx, entry, self.user)
        self.bank_tx.refresh_from_db()
        self.assertEqual(self.bank_tx.matched_journal_entry_id, entry.id)

    def test_match_different_journal_raises(self):
        entry = self._create_split_entry()
        other = create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=date(2026, 6, 18),
            description='Druga temeljnica',
            lines=[
                JournalLineInput('1000', Decimal('0'), Decimal('1980.00')),
                JournalLineInput('2145', Decimal('1980.00'), Decimal('0')),
            ],
            post=True,
        )
        match_transaction_to_journal_entry(self.bank_tx, entry, self.user)
        with self.assertRaises(ValidationError):
            match_transaction_to_journal_entry(self.bank_tx, other, self.user)

    def test_unmatch_transaction(self):
        entry = self._create_split_entry()
        match_transaction_to_journal_entry(self.bank_tx, entry, self.user)
        unmatch_transaction(self.bank_tx, self.user)
        self.bank_tx.refresh_from_db()
        self.assertIsNone(self.bank_tx.matched_journal_entry_id)
        self.assertEqual(self.bank_tx.match_status, 'unmatched')

    def test_unmatch_is_idempotent(self):
        unmatch_transaction(self.bank_tx, self.user)
        self.bank_tx.refresh_from_db()
        self.assertEqual(self.bank_tx.match_status, 'unmatched')

    def test_match_rejects_draft_entry(self):
        entry = create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=date(2026, 6, 18),
            description='Nacrt',
            lines=[
                JournalLineInput('2145', Decimal('2000.00'), Decimal('0')),
                JournalLineInput('1000', Decimal('0'), Decimal('1980.00')),
                JournalLineInput('7792', Decimal('0'), Decimal('20.00')),
            ],
            post=False,
        )
        with self.assertRaises(ValidationError):
            match_transaction_to_journal_entry(self.bank_tx, entry, self.user)

    def test_match_uses_bank_account_ledger_not_hardcoded(self):
        from accounting.models import ChartOfAccounts

        ledger_alt = ChartOfAccounts.all_objects.filter(
            tenant=self.tenant,
            account_code='1020',
            is_postable=True,
        ).first()
        if ledger_alt is None:
            self.skipTest('Konto 1020 nije dostupan u testnom kontnom planu')

        self.bank_account.ledger_account = ledger_alt
        self.bank_account.save(update_fields=['ledger_account'])

        wrong_entry = create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=date(2026, 6, 18),
            description='Krivi konto',
            lines=[
                JournalLineInput('2145', Decimal('1980.00'), Decimal('0')),
                JournalLineInput('1000', Decimal('0'), Decimal('1980.00')),
            ],
            post=True,
        )
        with self.assertRaises(ValidationError):
            match_transaction_to_journal_entry(self.bank_tx, wrong_entry, self.user)

        correct_entry = create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=date(2026, 6, 18),
            description='Ispravan ledger konto',
            lines=[
                JournalLineInput('2145', Decimal('1980.00'), Decimal('0')),
                JournalLineInput('1020', Decimal('0'), Decimal('1980.00')),
            ],
            post=True,
        )
        match_transaction_to_journal_entry(self.bank_tx, correct_entry, self.user)
        self.bank_tx.refresh_from_db()
        self.assertEqual(self.bank_tx.matched_journal_entry_id, correct_entry.id)

    def test_reverse_blocked_when_bank_matched(self):
        entry = self._create_split_entry()
        match_transaction_to_journal_entry(self.bank_tx, entry, self.user)
        with self.assertRaises(ValidationError):
            entry.reverse(self.user)

    def test_suggest_journal_matches(self):
        entry = self._create_split_entry()
        self.bank_tx.matched_journal_entry = None
        self.bank_tx.match_status = 'unmatched'
        self.bank_tx.save(update_fields=['matched_journal_entry', 'match_status'])
        count = suggest_journal_matches(self.tenant)
        self.assertEqual(count, 1)
        self.bank_tx.refresh_from_db()
        self.assertEqual(self.bank_tx.matched_journal_entry_id, entry.id)
        self.assertEqual(self.bank_tx.match_status, 'suggested')
