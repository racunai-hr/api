"""Close a draft expense from an exact unmatched bank debit."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import ChartOfAccounts, JournalEntryLine
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules, resolve_account
from accounting.services.rrif_import import import_rrif_chart
from banking.models import BankStatement, BankTransaction
from domains.finance.ai.account_suggest import suggest_expense_accounts
from domains.finance.services.subledger import get_subledger_item_for_source
from expenses.models import Expense, ExpenseCategory, ExpenseLine, SupplierLinePostingPattern
from partners.models import Partner
from payments.models import BankAccount
from tenants.models import Tenant, TenantMembership

HOST = 'closebank.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class ExpenseCloseFromBankTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='closebank', name='Close Bank')
        cls.other = Tenant.objects.create(slug='closebank-other', name='Other')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.owner = User.objects.create_user(username='closebank-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        cls.partner = Partner.all_objects.create(
            tenant=cls.tenant,
            name='FINA',
            partner_type='supplier',
            status='active',
            address='Ulica',
            city='Zagreb',
            postal_code='10000',
            country_code='HR',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.account_4100 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4100')
        cls.account_4123 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4123')
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
            statement_number='ST-CLOSE',
            statement_date=date(2026, 10, 7),
            opening_balance=Decimal('0'),
            closing_balance=Decimal('500'),
            status='imported',
            imported_by=cls.owner,
        )

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = HOST

    def _expense(self, number, description='Najam poslužitelja', amount='132.72'):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            supplier=self.partner,
            category=self.category,
            expense_number=number,
            expense_date=date(2026, 9, 30),
            amount=Decimal(amount),
            tax_amount=Decimal('0.00'),
            currency='EUR',
            status='draft',
            description=description,
            created_by=self.owner,
        )
        ExpenseLine.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            position=1,
            description=description,
            net_amount=Decimal(amount),
            vat_amount=Decimal('0.00'),
            gross_amount=Decimal(amount),
        )
        return expense

    def _debit(self, amount='132.72', **overrides):
        data = {
            'tenant': self.tenant,
            'bank_statement': self.statement,
            'transaction_date': date(2026, 10, 7),
            'amount': Decimal(amount),
            'currency': 'EUR',
            'transaction_type': 'debit',
            'description': 'Placanje FINA',
            'match_status': 'unmatched',
        }
        data.update(overrides)
        return BankTransaction.all_objects.create(**data)

    def test_header_without_lines_books_one_account(self):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            supplier=self.partner,
            category=self.category,
            expense_number='FINA-HEADER',
            expense_date=date(2026, 9, 30),
            amount=Decimal('132.72'),
            tax_amount=Decimal('0.00'),
            currency='EUR',
            status='draft',
            description='Najam poslužitelja',
            created_by=self.owner,
        )
        tx = self._debit()
        preview = self.client.get(f'/api/banking/expenses/{expense.pk}/close-candidates/')
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(len(preview.data['lines']), 1)
        response = self.client.post(
            f'/api/banking/expenses/{expense.pk}/close-from-bank/',
            {
                'bank_transaction_id': tx.pk,
                'remember': True,
                'lines': [{'position': 1, 'account_id': self.account_4100.pk}],
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='close-header',
        )
        self.assertEqual(response.status_code, 200, response.data)
        expense.refresh_from_db()
        self.assertEqual(expense.expense_account_id, self.account_4100.pk)
        self.assertEqual(expense.status, 'paid')

    def test_exact_amount_candidate_is_listed(self):
        expense = self._expense('FINA-1')
        exact = self._debit()
        self._debit('10.00')
        response = self.client.get(f'/api/banking/expenses/{expense.pk}/close-candidates/')
        self.assertEqual(response.status_code, 200, response.data)
        ids = [row['id'] for row in response.data['payments']]
        self.assertEqual(ids, [exact.pk])
        self.assertIn('amount_exact', response.data['payments'][0]['match_reasons'])
        self.assertFalse(response.data['remembered'])

    def test_post_books_chosen_account_and_remembers_the_pattern(self):
        expense = self._expense('FINA-2')
        tx = self._debit()
        response = self.client.post(
            f'/api/banking/expenses/{expense.pk}/close-from-bank/',
            {
                'bank_transaction_id': tx.pk,
                'remember': True,
                'lines': [{'position': 1, 'account_id': self.account_4100.pk}],
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='close-fina-2',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'paid')
        self.assertEqual(response.data['match_status'], 'matched')
        self.assertEqual(response.data['subledger_status'], 'closed')
        self.assertTrue(
            JournalEntryLine.objects.filter(
                account_id=self.account_4100.pk,
                debit_amount=Decimal('132.72'),
                journal_entry__source_object_id=expense.pk,
            ).exists()
        )
        item = get_subledger_item_for_source(self.tenant, expense)
        self.assertEqual(item.status, 'closed')
        pattern = SupplierLinePostingPattern.all_objects.get(
            tenant=self.tenant,
            supplier=self.partner,
        )
        self.assertEqual(pattern.accounts[0]['account_id'], self.account_4100.pk)

        replay = self.client.post(
            f'/api/banking/expenses/{expense.pk}/close-from-bank/',
            {
                'bank_transaction_id': tx.pk,
                'remember': True,
                'lines': [{'position': 1, 'account_id': self.account_4100.pk}],
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='close-fina-2',
        )
        self.assertEqual(replay.status_code, 200, replay.data)

        other = self._expense('FINA-2B')
        rejected = self.client.post(
            f'/api/banking/expenses/{other.pk}/close-from-bank/',
            {
                'bank_transaction_id': self._debit().pk,
                'remember': False,
                'lines': [{'position': 1, 'account_id': self.account_4100.pk}],
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='close-fina-2',
        )
        self.assertEqual(rejected.status_code, 409, rejected.data)
        other.refresh_from_db()
        self.assertEqual(other.status, 'draft')

    def test_remembered_pattern_books_without_a_new_suggestion(self):
        first = self._expense('FINA-3')
        self.client.post(
            f'/api/banking/expenses/{first.pk}/close-from-bank/',
            {
                'bank_transaction_id': self._debit().pk,
                'remember': True,
                'lines': [{'position': 1, 'account_id': self.account_4100.pk}],
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='close-fina-3',
        )
        second = self._expense('FINA-4')
        with patch('requests.post') as suggest:
            preview = self.client.get(f'/api/banking/expenses/{second.pk}/close-candidates/')
            suggest.assert_not_called()
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertTrue(preview.data['remembered'])
        self.assertEqual(preview.data['lines'][0]['account_id'], self.account_4100.pk)

        response = self.client.post(
            f'/api/banking/expenses/{second.pk}/close-from-bank/',
            {
                'bank_transaction_id': self._debit().pk,
                'remember': False,
                'lines': [{'position': 1, 'account_id': self.account_4123.pk}],
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='close-fina-4',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            JournalEntryLine.objects.filter(
                account_id=self.account_4100.pk,
                debit_amount=Decimal('132.72'),
                journal_entry__source_object_id=second.pk,
            ).exists()
        )
        self.assertFalse(
            JournalEntryLine.objects.filter(
                account_id=self.account_4123.pk,
                journal_entry__source_object_id=second.pk,
            ).exists()
        )

        different = self._expense('FINA-5', description='Druga stavka')
        with patch('requests.post') as suggest:
            preview = self.client.get(f'/api/banking/expenses/{different.pk}/close-candidates/')
            suggest.assert_not_called()
        self.assertFalse(preview.data['remembered'])

    def test_foreign_or_matched_payment_is_rejected(self):
        expense = self._expense('FINA-6', amount='20.00')
        matched = self._debit('20.00', match_status='matched')
        response = self.client.post(
            f'/api/banking/expenses/{expense.pk}/close-from-bank/',
            {
                'bank_transaction_id': matched.pk,
                'lines': [{'position': 1, 'account_id': self.account_4100.pk}],
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='close-fina-6',
        )
        self.assertEqual(response.status_code, 422, response.data)
        foreign = self._debit('20.00', tenant=self.other)
        missing = self.client.post(
            f'/api/banking/expenses/{expense.pk}/close-from-bank/',
            {
                'bank_transaction_id': foreign.pk,
                'lines': [{'position': 1, 'account_id': self.account_4100.pk}],
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='close-fina-6b',
        )
        self.assertEqual(missing.status_code, 404, missing.data)
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'draft')

    @patch('requests.post')
    def test_account_suggestion_does_not_call_openai(self, post):
        preview = suggest_expense_accounts(self._expense('FINA-NO-AI'))
        post.assert_not_called()
        self.assertFalse(preview['remembered'])
        self.assertIsNone(preview['lines'][0]['account_id'])
