"""ADR-0026 PrivateFundsClaim — supplier_payment + deposit_funding."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import Deposit, JournalEntry, PrivateFundsClaim, SubledgerItem
from accounting.services.analytics import get_or_create_analytic_for_partner
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules, resolve_account
from accounting.services.rrif_import import import_rrif_chart
from banking.models import BankStatement, BankTransaction
from domains.finance.services.deposits import create_deposit, post_deposit, return_deposit
from domains.finance.services.expenses import approve_expense_for_posting
from domains.finance.services.private_funds import (
    create_claim,
    ensure_partner_ante_vrcan,
    post_claim,
)
from domains.finance.services.subledger import get_subledger_item_for_source
from expenses.models import Expense, ExpenseCategory, ExpensePayer, ExpenseSource, SettlementMethod
from partners.models import Partner
from payments.models import BankAccount
from tenants.models import Tenant, TenantMembership

HOST = 'pfc.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class PrivateFundsClaimTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='pfc', name='PFC Co')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.owner = User.objects.create_user(username='pfc-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        cls.supplier = Partner.all_objects.create(
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
        cls.payer = ExpensePayer.all_objects.create(
            tenant=cls.tenant,
            name='Ante Vrcan',
            oib='11528564544',
            type='other',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
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
            statement_number='ST-PFC',
            statement_date=date(2026, 8, 20),
            opening_balance=Decimal('0'),
            closing_balance=Decimal('0'),
            status='imported',
            imported_by=cls.owner,
        )

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

    def _headers(self, key='pfc-1'):
        return {'HTTP_HOST': HOST, 'HTTP_IDEMPOTENCY_KEY': key}

    def _expense_partial(self, *, total=Decimal('1000.00'), bank_paid=Decimal('600.00')):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-PFC-0001',
            source=ExpenseSource.MANUAL,
            status='draft',
            category=self.category,
            supplier=self.supplier,
            amount=total,
            tax_amount=Decimal('0'),
            currency='EUR',
            expense_date=date(2026, 7, 1),
            due_date=date(2026, 7, 1),
            settlement_method=SettlementMethod.BUSINESS_ACCOUNT,
            description='PFC test',
            created_by=self.owner,
        )
        approve_expense_for_posting(tenant=self.tenant, expense_id=expense.pk, user=self.owner)
        expense.refresh_from_db()
        item = get_subledger_item_for_source(self.tenant, expense)
        self.assertIsNotNone(item)
        self.assertEqual(item.open_amount, total)

        # Partial bank settle via JE + allocate (simulate ADR-0025 without full bank path)
        from accounting.models import JournalEntry, JournalEntryLine
        from accounting.services.posting import _next_entry_number, get_or_create_fiscal_period
        from django.contrib.contenttypes.models import ContentType
        from domains.finance.services.subledger import allocate_payment

        analytic = get_or_create_analytic_for_partner(self.tenant, self.supplier, synthetic_code='2201')
        bank = resolve_account(self.tenant, '1000')
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number=_next_entry_number(self.tenant, date(2026, 7, 30)),
            entry_date=date(2026, 7, 30),
            status='draft',
            description='partial bank',
            is_auto=True,
            source_content_type=ContentType.objects.get_for_model(Expense),
            source_object_id=expense.pk,
            fiscal_period=get_or_create_fiscal_period(self.tenant, date(2026, 7, 30)),
            created_by=self.owner,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=analytic.chart_account,
            analytic_account=analytic,
            debit_amount=bank_paid,
            credit_amount=Decimal('0'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=bank,
            debit_amount=Decimal('0'),
            credit_amount=bank_paid,
        )
        entry.post(self.owner)
        allocate_payment(self.tenant, source=expense, journal_entry=entry, amount=bank_paid)
        item.refresh_from_db()
        expense.refresh_from_db()
        return expense, item

    def test_ensure_partner_links_expense_payer(self):
        partner = ensure_partner_ante_vrcan(tenant=self.tenant, user=self.owner)
        self.assertEqual(partner.tax_number, '11528564544')
        self.assertEqual(partner.partner_type, 'other')
        self.assertEqual(partner.country_code, 'HR')
        self.payer.refresh_from_db()
        self.assertEqual(self.payer.partner_id, partner.pk)
        again = ensure_partner_ante_vrcan(tenant=self.tenant, user=self.owner)
        self.assertEqual(again.pk, partner.pk)
        self.assertEqual(
            Partner.all_objects.filter(tenant=self.tenant, tax_number='11528564544').count(),
            1,
        )

    def test_supplier_payment_creditor_swap_one_je(self):
        ante = ensure_partner_ante_vrcan(tenant=self.tenant, user=self.owner)
        expense, item = self._expense_partial()
        remainder = item.open_amount
        self.assertEqual(remainder, Decimal('400.00'))

        dto = create_claim(
            tenant=self.tenant,
            user=self.owner,
            data={
                'partner_id': ante.pk,
                'claim_type': 'supplier_payment',
                'amount': str(remainder),
                'claim_date': date(2026, 8, 1),
                'related_type': 'expense',
                'related_id': expense.pk,
                'reference': 'Ante paid remainder',
            },
        )
        posted = post_claim(tenant=self.tenant, claim_id=dto['id'], user=self.owner)
        self.assertEqual(posted['status'], 'posted')
        self.assertEqual(posted['open_amount'], '400.00')

        claim = PrivateFundsClaim.all_objects.get(pk=dto['id'])
        entry = claim.journal_entry
        self.assertIsNotNone(entry)
        self.assertTrue(entry.description.startswith(f'[private_funds:{claim.pk}]'))
        self.assertEqual(entry.lines.count(), 2)
        credits = [ln for ln in entry.lines.all() if ln.credit_amount > 0]
        self.assertEqual(len(credits), 1)
        self.assertTrue(credits[0].account.account_code.startswith('2309-P'))
        ante_analytic = get_or_create_analytic_for_partner(self.tenant, ante, synthetic_code='2309')
        self.assertEqual(credits[0].account.account_code, ante_analytic.account_code)

        item.refresh_from_db()
        expense.refresh_from_db()
        self.assertEqual(item.open_amount, Decimal('0'))
        self.assertEqual(item.status, 'closed')
        self.assertEqual(expense.status, 'paid')

        ante_item = get_subledger_item_for_source(self.tenant, claim)
        self.assertIsNotNone(ante_item)
        self.assertEqual(ante_item.partner_id, ante.pk)
        self.assertEqual(ante_item.open_amount, Decimal('400.00'))
        self.assertEqual(ante_item.direction, 'payable')

    def test_deposit_funding_creates_ante_payable_without_touching_expense(self):
        ante = ensure_partner_ante_vrcan(tenant=self.tenant, user=self.owner)
        dep_dto = create_deposit(
            tenant=self.tenant,
            user=self.owner,
            data={
                'partner_id': self.supplier.pk,
                'amount': '5000.00',
                'deposit_date': date(2026, 7, 1),
                'reference': 'Kaution',
            },
        )
        post_deposit(tenant=self.tenant, deposit_id=dep_dto['id'], user=self.owner)
        return_deposit(
            tenant=self.tenant,
            user=self.owner,
            deposit_id=dep_dto['id'],
            data={
                'return_bank_account_id': self.bank.pk,
                'return_date': date(2026, 8, 18),
                'amount': '5000.00',
            },
        )
        deposit = Deposit.all_objects.get(pk=dep_dto['id'])
        self.assertEqual(deposit.status, Deposit.STATUS_RETURNED)

        expense = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-PFC-KEEP',
            source=ExpenseSource.MANUAL,
            status='approved',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('100.00'),
            tax_amount=Decimal('0'),
            currency='EUR',
            expense_date=date(2026, 7, 1),
            created_by=self.owner,
        )

        dto = create_claim(
            tenant=self.tenant,
            user=self.owner,
            data={
                'partner_id': ante.pk,
                'claim_type': 'deposit_funding',
                'amount': '5000.00',
                'claim_date': date(2026, 7, 1),
                'related_type': 'deposit',
                'related_id': deposit.pk,
            },
        )
        posted = post_claim(tenant=self.tenant, claim_id=dto['id'], user=self.owner)
        self.assertEqual(posted['open_amount'], '5000.00')

        expense.refresh_from_db()
        self.assertEqual(expense.status, 'approved')
        deposit.refresh_from_db()
        self.assertEqual(deposit.status, Deposit.STATUS_RETURNED)

        claim = PrivateFundsClaim.all_objects.get(pk=dto['id'])
        credit = claim.journal_entry.lines.filter(credit_amount__gt=0).first()
        self.assertTrue(credit.account.account_code.startswith('2309-P'))

    def test_bank_refund_closes_only_deposit_funding_claim(self):
        ante = ensure_partner_ante_vrcan(tenant=self.tenant, user=self.owner)
        expense2 = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-PFC-9900',
            source=ExpenseSource.MANUAL,
            status='draft',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('9900.00'),
            tax_amount=Decimal('0'),
            currency='EUR',
            expense_date=date(2026, 7, 1),
            due_date=date(2026, 7, 1),
            settlement_method=SettlementMethod.BUSINESS_ACCOUNT,
            description='SaM remainder sim',
            created_by=self.owner,
        )
        approve_expense_for_posting(tenant=self.tenant, expense_id=expense2.pk, user=self.owner)

        pay_dto = create_claim(
            tenant=self.tenant,
            user=self.owner,
            data={
                'partner_id': ante.pk,
                'claim_type': 'supplier_payment',
                'amount': '9900.00',
                'claim_date': date(2026, 8, 1),
                'related_type': 'expense',
                'related_id': expense2.pk,
            },
        )
        post_claim(tenant=self.tenant, claim_id=pay_dto['id'], user=self.owner)

        dep_dto = create_deposit(
            tenant=self.tenant,
            user=self.owner,
            data={
                'partner_id': self.supplier.pk,
                'amount': '5000.00',
                'deposit_date': date(2026, 7, 1),
            },
        )
        post_deposit(tenant=self.tenant, deposit_id=dep_dto['id'], user=self.owner)
        fund_dto = create_claim(
            tenant=self.tenant,
            user=self.owner,
            data={
                'partner_id': ante.pk,
                'claim_type': 'deposit_funding',
                'amount': '5000.00',
                'claim_date': date(2026, 7, 1),
                'related_type': 'deposit',
                'related_id': dep_dto['id'],
            },
        )
        post_claim(tenant=self.tenant, claim_id=fund_dto['id'], user=self.owner)

        fund_claim = PrivateFundsClaim.all_objects.get(pk=fund_dto['id'])
        fund_item = get_subledger_item_for_source(self.tenant, fund_claim)
        pay_claim = PrivateFundsClaim.all_objects.get(pk=pay_dto['id'])
        pay_item = get_subledger_item_for_source(self.tenant, pay_claim)
        self.assertEqual(pay_item.open_amount, Decimal('9900.00'))
        self.assertEqual(fund_item.open_amount, Decimal('5000.00'))

        tx = BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=date(2026, 8, 20),
            amount=Decimal('5000.00'),
            transaction_type='debit',
            description='Refund Ante kaucija',
            match_status='unmatched',
        )
        resp = self.client.post(
            f'/api/banking/transactions/{tx.pk}/reconcile-open-item/',
            {'subledger_item_id': fund_item.pk},
            format='json',
            **self._headers('refund-ante-5k'),
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        fund_item.refresh_from_db()
        pay_item.refresh_from_db()
        expense2.refresh_from_db()
        self.assertEqual(fund_item.open_amount, Decimal('0'))
        self.assertEqual(fund_item.status, 'closed')
        self.assertEqual(pay_item.open_amount, Decimal('9900.00'))
        self.assertEqual(expense2.status, 'paid')

        ante_open = SubledgerItem.all_objects.filter(
            tenant=self.tenant,
            partner=ante,
            status__in=('open', 'partial'),
        )
        self.assertEqual(sum(i.open_amount for i in ante_open), Decimal('9900.00'))
