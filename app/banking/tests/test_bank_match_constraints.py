"""Slice 2: match constraints, validation, concurrent match/unmatch."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import TestCase, TransactionTestCase

from accounting.models import JournalEntryLine
from accounting.services.chart import provision_tenant_chart
from accounting.services.manual_journal import JournalLineInput, create_manual_journal_entry
from accounting.services.posting import resolve_account
from banking.models import BankStatement, BankTransaction
from banking.reconciliation import (
    match_transaction,
    match_transaction_to_journal_entry,
    unmatch_transaction,
)
from banking.services.match_duplicates import raise_if_duplicate_bank_matches
from payments.models import BankAccount, Payment
from tenants.models import Tenant

KNOWN_IBAN = 'HR6124070001100204771'


def _create_payment(*, tenant, bank_account, user, amount, payment_type='incoming', currency='EUR', **kwargs):
    defaults = {
        'tenant': tenant,
        'payment_number': kwargs.pop('payment_number', f'PAY-{amount}-{payment_type}'),
        'payment_type': payment_type,
        'payment_method': 'bank_transfer',
        'status': 'completed',
        'amount': amount,
        'currency': currency,
        'bank_account': bank_account,
        'payment_date': date(2026, 8, 1),
        'created_by': user,
    }
    defaults.update(kwargs)
    return Payment.all_objects.create(**defaults)


class BankMatchConstraintModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='match-cstr', password='test')
        self.tenant = Tenant.objects.create(slug='match-cstr', name='Match Cstr')
        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni',
            bank_name='OTP',
            account_number='1',
            iban=KNOWN_IBAN,
            currency='EUR',
        )
        self.statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='MC-1',
            bank_account=self.bank_account,
            statement_date=date(2026, 8, 1),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('100.00'),
            imported_by=self.user,
        )

    def _tx(self, *, external_id, amount=Decimal('50.00'), tx_type='credit'):
        return BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 1),
            amount=amount,
            transaction_type=tx_type,
            description=external_id,
            external_id=external_id,
            currency='EUR',
        )

    def test_unique_matched_payment_constraint(self):
        payment = _create_payment(
            tenant=self.tenant,
            bank_account=self.bank_account,
            user=self.user,
            amount=Decimal('50.00'),
            payment_number='PAY-U1',
        )
        tx1 = self._tx(external_id='u1')
        tx2 = self._tx(external_id='u2')
        tx1.matched_payment = payment
        tx1.match_status = 'matched'
        tx1.save()
        tx2.matched_payment = payment
        tx2.match_status = 'matched'
        with self.assertRaises(IntegrityError):
            tx2.save()

    def test_unique_matched_journal_constraint(self):
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)
        provision_tenant_chart(self.tenant)
        ledger = resolve_account(self.tenant, '1000')
        self.bank_account.ledger_account = ledger
        self.bank_account.save(update_fields=['ledger_account'])

        entry = create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=date(2026, 8, 1),
            description='JE unique',
            lines=[
                JournalLineInput('1000', Decimal('50.00'), Decimal('0')),
                JournalLineInput('7792', Decimal('0'), Decimal('50.00')),
            ],
            post=True,
        )
        tx1 = self._tx(external_id='j1')
        tx2 = self._tx(external_id='j2')
        tx1.matched_journal_entry = entry
        tx1.match_status = 'matched'
        tx1.save()
        tx2.matched_journal_entry = entry
        tx2.match_status = 'matched'
        with self.assertRaises(IntegrityError):
            tx2.save()

    def test_at_most_one_match_target_check(self):
        payment = _create_payment(
            tenant=self.tenant,
            bank_account=self.bank_account,
            user=self.user,
            amount=Decimal('50.00'),
            payment_number='PAY-BOTH',
        )
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)
        provision_tenant_chart(self.tenant)
        ledger = resolve_account(self.tenant, '1000')
        self.bank_account.ledger_account = ledger
        self.bank_account.save(update_fields=['ledger_account'])
        entry = create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=date(2026, 8, 1),
            description='JE both',
            lines=[
                JournalLineInput('1000', Decimal('50.00'), Decimal('0')),
                JournalLineInput('7792', Decimal('0'), Decimal('50.00')),
            ],
            post=True,
        )
        tx = self._tx(external_id='both')
        tx.matched_payment = payment
        tx.matched_journal_entry = entry
        tx.match_status = 'matched'
        with self.assertRaises(IntegrityError):
            tx.save()

    def test_constraints_registered_on_table(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor,
                'banking_banktransaction',
            )
        self.assertIn('unique_banktx_matched_payment', constraints)
        self.assertIn('unique_banktx_matched_journal', constraints)
        self.assertIn('banktx_at_most_one_match_target', constraints)


class MatchDuplicatePreflightTests(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='match-pre', password='test')
        self.tenant = Tenant.objects.create(slug='match-pre', name='Match Pre')
        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni',
            bank_name='OTP',
            account_number='1',
            iban=KNOWN_IBAN,
        )
        self.statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='PRE-1',
            bank_account=self.bank_account,
            statement_date=date(2026, 8, 1),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('100.00'),
            imported_by=self.user,
        )

    def test_raise_if_duplicates_fail_closed(self):
        payment = _create_payment(
            tenant=self.tenant,
            bank_account=self.bank_account,
            user=self.user,
            amount=Decimal('50.00'),
            payment_number='PAY-DUP',
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
                    description=f'dup-{i}',
                    external_id=f'dup-{i}',
                    match_status='matched',
                    matched_payment=payment,
                )
            with self.assertRaises(RuntimeError) as ctx:
                raise_if_duplicate_bank_matches(BankTransaction.all_objects.all())
            self.assertIn('matched_payment duplicates', str(ctx.exception))
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


class PaymentMatchValidationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='pay-match', password='test')
        self.tenant = Tenant.objects.create(slug='pay-match', name='Pay Match')
        self.other = Tenant.objects.create(slug='pay-other', name='Pay Other')
        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni',
            bank_name='OTP',
            account_number='1',
            iban=KNOWN_IBAN,
            currency='EUR',
        )
        self.other_account = BankAccount.all_objects.create(
            tenant=self.other,
            account_name='Other',
            bank_name='OTP',
            account_number='2',
            iban='HR0012345678901234567',
            currency='EUR',
        )
        self.statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='PM-1',
            bank_account=self.bank_account,
            statement_date=date(2026, 8, 1),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('100.00'),
            imported_by=self.user,
        )
        self.bank_tx = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 1),
            amount=Decimal('100.00'),
            transaction_type='credit',
            description='uplata',
            currency='EUR',
        )

    def test_match_payment_success(self):
        payment = _create_payment(
            tenant=self.tenant,
            bank_account=self.bank_account,
            user=self.user,
            amount=Decimal('100.00'),
            payment_number='PAY-OK',
            payment_type='incoming',
        )
        match_transaction(self.bank_tx, payment)
        self.bank_tx.refresh_from_db()
        self.assertEqual(self.bank_tx.match_status, 'matched')
        self.assertEqual(self.bank_tx.matched_payment_id, payment.pk)

    def test_rejects_wrong_amount(self):
        payment = _create_payment(
            tenant=self.tenant,
            bank_account=self.bank_account,
            user=self.user,
            amount=Decimal('99.00'),
            payment_number='PAY-AMT',
        )
        with self.assertRaises(ValidationError):
            match_transaction(self.bank_tx, payment)

    def test_rejects_wrong_currency(self):
        payment = _create_payment(
            tenant=self.tenant,
            bank_account=self.bank_account,
            user=self.user,
            amount=Decimal('100.00'),
            payment_number='PAY-CCY',
            currency='USD',
        )
        with self.assertRaises(ValidationError):
            match_transaction(self.bank_tx, payment)

    def test_rejects_wrong_direction(self):
        payment = _create_payment(
            tenant=self.tenant,
            bank_account=self.bank_account,
            user=self.user,
            amount=Decimal('100.00'),
            payment_number='PAY-DIR',
            payment_type='outgoing',
        )
        with self.assertRaises(ValidationError):
            match_transaction(self.bank_tx, payment)

    def test_rejects_cross_tenant(self):
        payment = _create_payment(
            tenant=self.other,
            bank_account=self.other_account,
            user=self.user,
            amount=Decimal('100.00'),
            payment_number='PAY-XT',
        )
        with self.assertRaises(ValidationError):
            match_transaction(self.bank_tx, payment)

    def test_match_is_idempotent(self):
        payment = _create_payment(
            tenant=self.tenant,
            bank_account=self.bank_account,
            user=self.user,
            amount=Decimal('100.00'),
            payment_number='PAY-IDEM',
        )
        match_transaction(self.bank_tx, payment)
        match_transaction(self.bank_tx, payment)
        self.bank_tx.refresh_from_db()
        self.assertEqual(self.bank_tx.matched_payment_id, payment.pk)


class JournalMatchExtraValidationTests(TestCase):
    def setUp(self):
        import_rrif = __import__(
            'accounting.services.rrif_import',
            fromlist=['import_rrif_chart'],
        ).import_rrif_chart
        import_rrif(clear=True)
        self.tenant = Tenant.objects.create(slug='je-extra', name='JE Extra')
        provision_tenant_chart(self.tenant)
        User = get_user_model()
        self.user = User.objects.create_user(username='je-extra', password='test')
        ledger = resolve_account(self.tenant, '1000')
        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni',
            bank_name='OTP',
            account_number=KNOWN_IBAN,
            iban=KNOWN_IBAN,
            ledger_account=ledger,
        )
        self.statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='JE-1',
            bank_account=self.bank_account,
            statement_date=date(2026, 8, 1),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('100.00'),
            imported_by=self.user,
        )
        self.bank_tx = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 1),
            amount=Decimal('50.00'),
            transaction_type='credit',
            description='uplata',
        )

    def test_rejects_multiple_matching_bank_lines(self):
        entry = create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=date(2026, 8, 1),
            description='two bank lines',
            lines=[
                JournalLineInput('1000', Decimal('50.00'), Decimal('0')),
                JournalLineInput('2145', Decimal('0'), Decimal('50.00')),
            ],
            post=True,
        )
        # Duplicate an identical bank line by inserting another line on 1000.
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=resolve_account(self.tenant, '1000'),
            debit_amount=Decimal('50.00'),
            credit_amount=Decimal('0'),
            description='dup bank',
        )

        with self.assertRaises(ValidationError) as ctx:
            match_transaction_to_journal_entry(self.bank_tx, entry, self.user)
        self.assertIn('odgovarajuće stavke', str(ctx.exception))
        self.assertIn('točno jednu', str(ctx.exception))


class ConcurrentPaymentMatchTests(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='conc-match', password='test')
        self.tenant = Tenant.objects.create(slug='conc-match', name='Conc Match')
        self.bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni',
            bank_name='OTP',
            account_number='1',
            iban=KNOWN_IBAN,
            currency='EUR',
        )
        self.statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            statement_number='CM-1',
            bank_account=self.bank_account,
            statement_date=date(2026, 8, 1),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('200.00'),
            imported_by=self.user,
        )
        self.tx_a = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 1),
            amount=Decimal('100.00'),
            transaction_type='credit',
            description='a',
            external_id='conc-a',
            currency='EUR',
        )
        self.tx_b = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 1),
            amount=Decimal('100.00'),
            transaction_type='credit',
            description='b',
            external_id='conc-b',
            currency='EUR',
        )
        self.payment = _create_payment(
            tenant=self.tenant,
            bank_account=self.bank_account,
            user=self.user,
            amount=Decimal('100.00'),
            payment_number='PAY-CONC',
        )

    def test_parallel_match_same_payment_one_wins(self):
        errors = []
        successes = []

        def worker(tx_id):
            try:
                tx = BankTransaction.all_objects.get(pk=tx_id)
                match_transaction(tx, self.payment)
                successes.append(tx_id)
            except Exception as exc:  # noqa: BLE001 — collect for assertion
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, [self.tx_a.pk, self.tx_b.pk]))

        matched = BankTransaction.all_objects.filter(matched_payment=self.payment)
        self.assertEqual(matched.count(), 1)
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(
            any(isinstance(e, (ValidationError, IntegrityError)) for e in errors)
        )

    def test_parallel_match_and_unmatch(self):
        match_transaction(self.tx_a, self.payment)
        results = []

        def do_unmatch():
            try:
                unmatch_transaction(self.tx_a, self.user)
                results.append('unmatched')
            except Exception as exc:  # noqa: BLE001
                results.append(exc)

        def do_rematch():
            try:
                match_transaction(self.tx_a, self.payment)
                results.append('matched')
            except Exception as exc:  # noqa: BLE001
                results.append(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(do_unmatch), pool.submit(do_rematch)]
            for fut in futures:
                fut.result(timeout=30)
        self.tx_a.refresh_from_db()
        self.assertIn(self.tx_a.match_status, ('matched', 'unmatched'))
        if self.tx_a.match_status == 'matched':
            self.assertEqual(self.tx_a.matched_payment_id, self.payment.pk)
        else:
            self.assertIsNone(self.tx_a.matched_payment_id)
        self.assertTrue(all(isinstance(r, str) for r in results))
