"""Acceptance tests for ADR-0025 bank reconcile → open SubledgerItem."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import Deposit, JournalEntry, SubledgerItem, VATLedgerEntry
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import get_or_create_fiscal_period, resolve_account
from accounting.services.rrif_import import import_rrif_chart
from banking.models import BankStatement, BankTransaction
from domains.finance.services.deposits import create_deposit, post_deposit
from domains.finance.services.subledger import create_subledger_item, get_subledger_item_for_source
from expenses.models import Expense, ExpenseCategory
from partners.models import Partner
from payments.models import BankAccount
from tenants.models import Tenant, TenantMembership

HOST = 'reconcile.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class BankReconcileOpenItemTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='reconcile', name='Reconcile Co')
        provision_tenant_chart(cls.tenant)
        User = get_user_model()
        cls.owner = User.objects.create_user(username='rec-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        cls.partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='SaM Automobile',
            tax_number='',
            vat_number='DE355497142',
            partner_type='supplier',
            status='active',
            address='X',
            city='Sinsheim',
            postal_code='74889',
            country_code='DE',
        )
        cls.bank = BankAccount.all_objects.create(
            tenant=cls.tenant,
            account_name='Žiro',
            bank_name='OTP',
            account_number='1',
            iban='HR6124070001100204771',
            currency='EUR',
            status='active',
            ledger_account=resolve_account(cls.tenant, '1000'),
        )
        cls.statement = BankStatement.all_objects.create(
            tenant=cls.tenant,
            bank_account=cls.bank,
            statement_number='ST-1',
            statement_date=date(2026, 8, 18),
            opening_balance=Decimal('0'),
            closing_balance=Decimal('5000'),
            status='imported',
            imported_by=cls.owner,
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = HOST

    def _credit_tx(self, amount='5000.00', tx_date=None):
        return BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=tx_date or date(2026, 8, 18),
            amount=Decimal(amount),
            currency='EUR',
            transaction_type='credit',
            description='Rueckerstattung Kaution',
            match_status='unmatched',
        )

    def _debit_tx(self, amount='1000.00', tx_date=None):
        return BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=tx_date or date(2026, 8, 18),
            amount=Decimal(amount),
            currency='EUR',
            transaction_type='debit',
            description='Payment supplier',
            match_status='unmatched',
        )

    def _open_deposit(self, amount='5000.00'):
        dto = create_deposit(
            tenant=self.tenant,
            user=self.owner,
            data={
                'partner_id': self.partner.pk,
                'amount': amount,
                'currency': 'EUR',
                'deposit_date': '2026-07-30',
                'reference': 'SaM Kaution',
            },
        )
        return post_deposit(tenant=self.tenant, deposit_id=dto['id'], user=self.owner)

    def test_candidates_and_deposit_reconcile(self):
        deposit = self._open_deposit()
        deposit_id = deposit['id']
        item = get_subledger_item_for_source(
            self.tenant, Deposit.all_objects.get(pk=deposit_id)
        )
        self.assertIsNotNone(item)
        tx = self._credit_tx()

        candidates = self.client.get(f'/api/banking/transactions/{tx.pk}/open-item-candidates/')
        self.assertEqual(candidates.status_code, 200, candidates.data)
        ids = [row['item_id'] for row in candidates.data['results']]
        self.assertIn(item.pk, ids)

        response = self.client.post(
            f'/api/banking/transactions/{tx.pk}/reconcile-open-item/',
            {'subledger_item_id': item.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='rec-dep-1',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['match_status'], 'matched')
        dep = Deposit.all_objects.get(pk=deposit_id)
        self.assertEqual(dep.status, 'returned')
        item.refresh_from_db()
        self.assertEqual(item.status, 'closed')
        self.assertEqual(item.open_amount, Decimal('0.00'))
        self.assertEqual(
            VATLedgerEntry.all_objects.filter(
                source_content_type=ContentType.objects.get_for_model(Deposit),
                source_object_id=deposit_id,
            ).count(),
            0,
        )

    def test_idempotency_replay_and_conflict(self):
        deposit = self._open_deposit()
        item = get_subledger_item_for_source(
            self.tenant, Deposit.all_objects.get(pk=deposit['id'])
        )
        tx = self._credit_tx()
        first = self.client.post(
            f'/api/banking/transactions/{tx.pk}/reconcile-open-item/',
            {'subledger_item_id': item.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='same-key',
        )
        self.assertEqual(first.status_code, 200)
        je_count = JournalEntry.all_objects.filter(
            description__contains='[deposit_returned:',
        ).count()
        second = self.client.post(
            f'/api/banking/transactions/{tx.pk}/reconcile-open-item/',
            {'subledger_item_id': item.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='same-key',
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            JournalEntry.all_objects.filter(description__contains='[deposit_returned:').count(),
            je_count,
        )

        other_dep = self._open_deposit(amount='5000.00')
        # Need another credit tx for second deposit — create separate open deposit with different amount path
        # Use same key with different item on SAME already-matched tx → 409 idempotency OR already reconciled
        other_item = get_subledger_item_for_source(
            self.tenant, Deposit.all_objects.get(pk=other_dep['id'])
        )
        conflict = self.client.post(
            f'/api/banking/transactions/{tx.pk}/reconcile-open-item/',
            {'subledger_item_id': other_item.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='same-key',
        )
        self.assertEqual(conflict.status_code, 409)

        already = self.client.post(
            f'/api/banking/transactions/{tx.pk}/reconcile-open-item/',
            {'subledger_item_id': item.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='new-key',
        )
        self.assertEqual(already.status_code, 409)

    def test_expense_debit_candidate_and_close(self):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            supplier=self.partner,
            category=self.category,
            expense_number='EXP-1',
            expense_date=date(2026, 8, 1),
            amount=Decimal('1000.00'),
            tax_amount=Decimal('0'),
            currency='EUR',
            status='approved',
            settlement_method='business_account',
            description='Supplier invoice fixture',
            created_by=self.owner,
        )
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202608-EXP',
            entry_date=date(2026, 8, 1),
            status='posted',
            description='[expense_approved] fixture',
            is_auto=True,
            fiscal_period=get_or_create_fiscal_period(self.tenant, date(2026, 8, 1)),
            created_by=self.owner,
        )
        create_subledger_item(
            self.tenant,
            partner=self.partner,
            direction='payable',
            source=expense,
            journal_entry=entry,
            amount=Decimal('1000.00'),
            due_date=date(2026, 8, 1),
        )
        item = get_subledger_item_for_source(self.tenant, expense)
        tx = self._debit_tx('1000.00')
        candidates = self.client.get(f'/api/banking/transactions/{tx.pk}/open-item-candidates/')
        self.assertIn(item.pk, [r['item_id'] for r in candidates.data['results']])
        response = self.client.post(
            f'/api/banking/transactions/{tx.pk}/reconcile-open-item/',
            {'subledger_item_id': item.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='exp-1',
        )
        self.assertEqual(response.status_code, 200, response.data)
        item.refresh_from_db()
        self.assertEqual(item.status, 'closed')
        self.assertEqual(response.data['match_status'], 'matched')
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'paid')

    def test_expense_partial_then_full_close_sets_paid(self):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            supplier=self.partner,
            category=self.category,
            expense_number='EXP-PARTIAL',
            expense_date=date(2026, 8, 1),
            amount=Decimal('1000.00'),
            tax_amount=Decimal('0'),
            currency='EUR',
            status='approved',
            settlement_method='business_account',
            description='Partial AP',
            created_by=self.owner,
        )
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202608-EXP-P',
            entry_date=date(2026, 8, 1),
            status='posted',
            description='[expense_approved] partial fixture',
            is_auto=True,
            fiscal_period=get_or_create_fiscal_period(self.tenant, date(2026, 8, 1)),
            created_by=self.owner,
        )
        create_subledger_item(
            self.tenant,
            partner=self.partner,
            direction='payable',
            source=expense,
            journal_entry=entry,
            amount=Decimal('1000.00'),
            due_date=date(2026, 8, 1),
        )
        item = get_subledger_item_for_source(self.tenant, expense)
        tx1 = self._debit_tx('600.00')
        first = self.client.post(
            f'/api/banking/transactions/{tx1.pk}/reconcile-open-item/',
            {'subledger_item_id': item.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='partial-1',
        )
        self.assertEqual(first.status_code, 200, first.data)
        item.refresh_from_db()
        expense.refresh_from_db()
        self.assertEqual(item.status, 'partial')
        self.assertEqual(item.open_amount, Decimal('400.00'))
        self.assertEqual(expense.status, 'approved')

        tx2 = self._debit_tx('400.00')
        second = self.client.post(
            f'/api/banking/transactions/{tx2.pk}/reconcile-open-item/',
            {'subledger_item_id': item.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='partial-2',
        )
        self.assertEqual(second.status_code, 200, second.data)
        item.refresh_from_db()
        expense.refresh_from_db()
        self.assertEqual(item.status, 'closed')
        self.assertEqual(item.open_amount, Decimal('0.00'))
        self.assertEqual(expense.status, 'paid')

    def test_partial_amount_exceeds_open_after_lock(self):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            supplier=self.partner,
            category=self.category,
            expense_number='EXP-OVER',
            expense_date=date(2026, 8, 1),
            amount=Decimal('1000.00'),
            tax_amount=Decimal('0'),
            currency='EUR',
            status='approved',
            settlement_method='business_account',
            description='Over amount',
            created_by=self.owner,
        )
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202608-EXP-O',
            entry_date=date(2026, 8, 1),
            status='posted',
            description='[expense_approved] over fixture',
            is_auto=True,
            fiscal_period=get_or_create_fiscal_period(self.tenant, date(2026, 8, 1)),
            created_by=self.owner,
        )
        create_subledger_item(
            self.tenant,
            partner=self.partner,
            direction='payable',
            source=expense,
            journal_entry=entry,
            amount=Decimal('1000.00'),
            due_date=date(2026, 8, 1),
        )
        item = get_subledger_item_for_source(self.tenant, expense)
        tx = self._debit_tx('1200.00')
        response = self.client.post(
            f'/api/banking/transactions/{tx.pk}/reconcile-open-item/',
            {'subledger_item_id': item.pk},
            format='json',
            HTTP_IDEMPOTENCY_KEY='over-1',
        )
        self.assertEqual(response.status_code, 422)

    def test_rollback_when_match_fails(self):
        deposit = self._open_deposit()
        item = get_subledger_item_for_source(
            self.tenant, Deposit.all_objects.get(pk=deposit['id'])
        )
        tx = self._credit_tx()
        with patch(
            'domains.banking.write.reconcile.match_transaction_to_journal_entry',
            side_effect=Exception('forced match failure'),
        ):
            with self.assertRaises(Exception):
                from domains.banking.write.reconcile import reconcile_open_item_api

                reconcile_open_item_api(
                    tenant=self.tenant,
                    user=self.owner,
                    transaction_id=tx.pk,
                    subledger_item_id=item.pk,
                    idempotency_key='rollback-1',
                )
        dep = Deposit.all_objects.get(pk=deposit['id'])
        self.assertEqual(dep.status, 'open')
        item.refresh_from_db()
        self.assertEqual(item.status, 'open')
        self.assertEqual(item.open_amount, Decimal('5000.00'))
        tx.refresh_from_db()
        self.assertEqual(tx.match_status, 'unmatched')
        self.assertIsNone(tx.matched_journal_entry_id)
        self.assertEqual(
            JournalEntry.all_objects.filter(description__contains='[deposit_returned:').count(),
            0,
        )


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class BankReconcileOpenItemRaceTests(TransactionTestCase):
    """Two concurrent keys must produce exactly one settlement JE."""

    def setUp(self):
        from accounting.services.chart import provision_tenant_chart
        from accounting.services.posting import resolve_account
        from accounting.services.rrif_import import import_rrif_chart

        import_rrif_chart(clear=True)
        self.tenant = Tenant.objects.create(slug='reconcile-race', name='Race Co')
        provision_tenant_chart(self.tenant)
        User = get_user_model()
        self.owner = User.objects.create_user(username='rec-race-owner', password='test')
        TenantMembership.objects.create(user=self.owner, tenant=self.tenant, role='owner')
        self.partner = Partner.all_objects.create(
            tenant=self.tenant,
            name='Race Partner',
            tax_number='',
            vat_number='DE123456789',
            partner_type='supplier',
            status='active',
            address='X',
            city='Berlin',
            postal_code='10115',
            country_code='DE',
        )
        self.bank = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Žiro',
            bank_name='OTP',
            account_number='2',
            iban='HR6124070001100204772',
            currency='EUR',
            status='active',
            ledger_account=resolve_account(self.tenant, '1000'),
        )
        self.statement = BankStatement.all_objects.create(
            tenant=self.tenant,
            bank_account=self.bank,
            statement_number='ST-RACE',
            statement_date=date(2026, 8, 18),
            opening_balance=Decimal('0'),
            closing_balance=Decimal('5000'),
            status='imported',
            imported_by=self.owner,
        )

    def test_parallel_keys_one_settlement(self):
        import threading

        from domains.banking.write.reconcile import reconcile_open_item_api
        from domains.finance.services.deposits import create_deposit, post_deposit
        from domains.finance.services.subledger import get_subledger_item_for_source

        dto = create_deposit(
            tenant=self.tenant,
            user=self.owner,
            data={
                'partner_id': self.partner.pk,
                'amount': '5000.00',
                'currency': 'EUR',
                'deposit_date': '2026-07-30',
                'reference': 'Race',
            },
        )
        post_deposit(tenant=self.tenant, deposit_id=dto['id'], user=self.owner)
        item = get_subledger_item_for_source(
            self.tenant, Deposit.all_objects.get(pk=dto['id'])
        )
        tx = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 18),
            amount=Decimal('5000.00'),
            currency='EUR',
            transaction_type='credit',
            description='Race credit',
            match_status='unmatched',
        )

        results: list[str] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def worker(key: str):
            try:
                barrier.wait(timeout=5)
                reconcile_open_item_api(
                    tenant=self.tenant,
                    user=self.owner,
                    transaction_id=tx.pk,
                    subledger_item_id=item.pk,
                    idempotency_key=key,
                )
                results.append('ok')
            except BaseException as exc:  # noqa: BLE001 — collect race outcomes
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=('race-a',))
        t2 = threading.Thread(target=worker, args=('race-b',))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        returned_jes = JournalEntry.all_objects.filter(
            tenant=self.tenant,
            description__contains=f'[deposit_returned:{dto["id"]}]',
            status='posted',
        ).count()
        self.assertEqual(returned_jes, 1, f'results={results} errors={errors!r}')
        dep = Deposit.all_objects.get(pk=dto['id'])
        self.assertEqual(dep.status, 'returned')
        tx.refresh_from_db()
        self.assertEqual(tx.match_status, 'matched')
        # Exactly one success; the loser must conflict (already matched / lock race)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)

    def test_parallel_partials_one_succeeds_other_exceeds_open(self):
        import threading

        from accounting.models import SubledgerAllocation
        from accounting.services.posting import get_or_create_fiscal_period
        from django.db.models import Sum
        from domains.banking.write.reconcile import reconcile_open_item_api
        from domains.finance.services.subledger import create_subledger_item, get_subledger_item_for_source
        from expenses.models import Expense, ExpenseCategory

        category = ExpenseCategory.all_objects.create(tenant=self.tenant, name='Race Ostalo')
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            supplier=self.partner,
            category=category,
            expense_number='EXP-RACE',
            expense_date=date(2026, 8, 1),
            amount=Decimal('1000.00'),
            tax_amount=Decimal('0'),
            currency='EUR',
            status='approved',
            settlement_method='business_account',
            description='Race partial',
            created_by=self.owner,
        )
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202608-RACE',
            entry_date=date(2026, 8, 1),
            status='posted',
            description='[expense_approved] race',
            is_auto=True,
            fiscal_period=get_or_create_fiscal_period(self.tenant, date(2026, 8, 1)),
            created_by=self.owner,
        )
        create_subledger_item(
            self.tenant,
            partner=self.partner,
            direction='payable',
            source=expense,
            journal_entry=entry,
            amount=Decimal('1000.00'),
            due_date=date(2026, 8, 1),
        )
        item = get_subledger_item_for_source(self.tenant, expense)
        tx_a = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 18),
            amount=Decimal('600.00'),
            currency='EUR',
            transaction_type='debit',
            description='Race A',
            match_status='unmatched',
            external_id='race-a',
        )
        tx_b = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 18),
            amount=Decimal('600.00'),
            currency='EUR',
            transaction_type='debit',
            description='Race B',
            match_status='unmatched',
            external_id='race-b',
        )

        results: list[int] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def worker(tx_id: int, key: str):
            try:
                barrier.wait(timeout=5)
                reconcile_open_item_api(
                    tenant=self.tenant,
                    user=self.owner,
                    transaction_id=tx_id,
                    subledger_item_id=item.pk,
                    idempotency_key=key,
                )
                results.append(tx_id)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=(tx_a.pk, 'partial-race-a'))
        t2 = threading.Thread(target=worker, args=(tx_b.pk, 'partial-race-b'))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        item.refresh_from_db()
        allocated = SubledgerAllocation.all_objects.filter(subledger_item=item).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0')
        self.assertEqual(len(results), 1, f'results={results} errors={errors!r}')
        self.assertEqual(len(errors), 1)
        self.assertEqual(item.open_amount, Decimal('400.00'))
        self.assertEqual(item.status, 'partial')
        self.assertEqual(allocated, Decimal('600.00'))
