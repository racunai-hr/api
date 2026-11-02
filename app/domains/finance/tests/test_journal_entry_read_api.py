"""Read-only journal entry list API (Glavna knjiga v1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import (
    AccountType,
    ChartOfAccounts,
    Deposit,
    FixedAsset,
    JournalEntry,
    JournalEntryLine,
    OfficialDocument,
)
from banking.models import BankStatement, BankTransaction
from expenses.models import Expense, ExpenseCategory
from invoices.models import Invoice
from partners.models import Partner
from payments.models import BankAccount, Payment
from tenants.models import Tenant, TenantMembership

HOST = 'jeread.racunai.hr'
OTHER_HOST = 'jeother.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class JournalEntryReadApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='jeread', name='JE Read Co')
        cls.other = Tenant.objects.create(slug='jeother', name='JE Other Co')
        User = get_user_model()
        cls.viewer = User.objects.create_user(username='je-viewer', password='test')
        cls.outsider = User.objects.create_user(username='je-outsider', password='test')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        TenantMembership.objects.create(user=cls.outsider, tenant=cls.other, role='viewer')

        asset_type = AccountType.all_objects.create(tenant=cls.tenant, name='asset')
        cls.account = ChartOfAccounts.all_objects.create(
            tenant=cls.tenant,
            account_code='1000',
            account_name='Banka',
            account_type=asset_type,
            is_postable=True,
        )
        other_type = AccountType.all_objects.create(tenant=cls.other, name='asset')
        other_account = ChartOfAccounts.all_objects.create(
            tenant=cls.other,
            account_code='1000',
            account_name='Banka',
            account_type=other_type,
            is_postable=True,
        )

        invoice_ct = ContentType.objects.get_for_model(Invoice)
        expense_ct = ContentType.objects.get_for_model(Expense)
        asset_ct = ContentType.objects.get_for_model(FixedAsset)
        official_ct = ContentType.objects.get_for_model(OfficialDocument)
        payment_ct = ContentType.objects.get_for_model(Payment)
        deposit_ct = ContentType.objects.get_for_model(Deposit)

        customer = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Kupac JE',
            tax_number='11111111111',
            partner_type='customer',
            status='active',
            country_code='HR',
            country='Hrvatska',
        )
        supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljač JE',
            tax_number='22222222222',
            partner_type='supplier',
            status='active',
            country_code='HR',
            country='Hrvatska',
        )
        issuer = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Carinska uprava',
            tax_number='18683136487',
            partner_type='supplier',
            status='active',
            country_code='HR',
            country='Hrvatska',
        )
        other_issuer = Partner.all_objects.create(
            tenant=cls.other,
            name='Tuđi izdavatelj',
            tax_number='33333333333',
            partner_type='supplier',
            status='active',
            country_code='HR',
            country='Hrvatska',
        )
        category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

        cls.invoice = Invoice.all_objects.create(
            tenant=cls.tenant,
            invoice_number='INV-001/2026',
            company_to=customer,
            issue_date=date(2026, 7, 27),
            due_date=date(2026, 8, 10),
            status='sent',
            subtotal=Decimal('20000.00'),
            tax_amount=Decimal('5000.00'),
            total_amount=Decimal('25000.00'),
            created_by=cls.viewer,
        )
        cls.expense = Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0001',
            status='approved',
            category=category,
            supplier=supplier,
            amount=Decimal('50.00'),
            tax_amount=Decimal('0'),
            expense_date=date(2026, 7, 15),
            description='Ulazni račun',
            created_by=cls.viewer,
        )
        cls.official = OfficialDocument.all_objects.create(
            tenant=cls.tenant,
            kind=OfficialDocument.KIND_TAX_DECISION,
            issuer=issuer,
            document_number='UP/I-410-22/26-09/36939',
            issue_date=date(2026, 6, 17),
            amount=Decimal('1051.04'),
            created_by=cls.viewer,
        )
        cls.other_official = OfficialDocument.all_objects.create(
            tenant=cls.other,
            kind=OfficialDocument.KIND_TAX_DECISION,
            issuer=other_issuer,
            document_number='UP/I-OTHER-TENANT',
            issue_date=date(2026, 6, 17),
            amount=Decimal('10.00'),
        )
        cls.deposit = Deposit.all_objects.create(
            tenant=cls.tenant,
            number='DEP-JE-1',
            partner=supplier,
            amount=Decimal('100.00'),
            deposit_date=date(2026, 7, 1),
            created_by=cls.viewer,
        )

        cls.multi = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202607-MULTI',
            description='Više linija',
            status='posted',
            entry_date=date(2026, 7, 30),
        )
        JournalEntryLine.objects.create(
            journal_entry=cls.multi,
            account=cls.account,
            debit_amount=Decimal('100.00'),
            credit_amount=Decimal('0'),
        )
        JournalEntryLine.objects.create(
            journal_entry=cls.multi,
            account=cls.account,
            debit_amount=Decimal('25.50'),
            credit_amount=Decimal('0'),
        )
        JournalEntryLine.objects.create(
            journal_entry=cls.multi,
            account=cls.account,
            debit_amount=Decimal('0'),
            credit_amount=Decimal('125.50'),
        )

        cls.invoice_je = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202607-INV',
            description='Izlazni račun',
            status='posted',
            entry_date=date(2026, 7, 27),
            is_auto=True,
            source_content_type=invoice_ct,
            source_object_id=cls.invoice.pk,
        )
        cls._balanced_lines(cls.invoice_je, cls.account, Decimal('25000.00'))

        cls.expense_je = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202607-EXP',
            description='Ulazni račun',
            status='posted',
            entry_date=date(2026, 7, 15),
            is_auto=True,
            source_content_type=expense_ct,
            source_object_id=cls.expense.pk,
        )
        cls._balanced_lines(cls.expense_je, cls.account, Decimal('50.00'))

        cls.manual = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202608-MAN',
            description='PPMV obveza A8',
            reference='WAUZZZF86RN003268',
            status='posted',
            entry_date=date(2026, 8, 3),
            is_auto=False,
        )
        cls._balanced_lines(cls.manual, cls.account, Decimal('10347.20'))

        cls.asset_je = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202608-ASSET',
            description='Aktivacija GFK',
            status='posted',
            entry_date=date(2026, 8, 1),
            is_auto=False,
            source_content_type=asset_ct,
            source_object_id=1,
        )
        cls._balanced_lines(cls.asset_je, cls.account, Decimal('10.00'))

        cls.official_je = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202606-OFF',
            description='[official_document_posted] rješenje',
            status='posted',
            entry_date=date(2026, 6, 17),
            is_auto=True,
            source_content_type=official_ct,
            source_object_id=cls.official.pk,
        )
        cls._balanced_lines(cls.official_je, cls.account, Decimal('1051.04'))

        cls.deposit_je = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202607-DEP',
            description='Kaucija',
            status='posted',
            entry_date=date(2026, 7, 1),
            is_auto=True,
            source_content_type=deposit_ct,
            source_object_id=cls.deposit.pk,
        )
        cls._balanced_lines(cls.deposit_je, cls.account, Decimal('100.00'))

        cls.cross_tenant_source_je = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202607-XTEN',
            description='GFK na tuđi dokument',
            status='posted',
            entry_date=date(2026, 7, 2),
            is_auto=True,
            source_content_type=official_ct,
            source_object_id=cls.other_official.pk,
        )
        cls._balanced_lines(cls.cross_tenant_source_je, cls.account, Decimal('10.00'))

        cls.dangling_je = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202607-DANG',
            description='Dangling GFK',
            status='posted',
            entry_date=date(2026, 7, 3),
            is_auto=True,
            source_content_type=invoice_ct,
            source_object_id=999999,
        )
        cls._balanced_lines(cls.dangling_je, cls.account, Decimal('1.00'))

        cls.draft = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202608-DRAFT',
            description='Nacrt',
            status='draft',
            entry_date=date(2026, 8, 10),
            is_auto=False,
        )

        bank = BankAccount.all_objects.create(
            tenant=cls.tenant,
            account_name='Žiro',
            bank_name='OTP',
            account_number='1',
            iban='HR6124070001100204771',
            currency='EUR',
            status='active',
        )
        statement = BankStatement.all_objects.create(
            tenant=cls.tenant,
            bank_account=bank,
            statement_number='ST-JE',
            statement_date=date(2026, 8, 6),
            opening_balance=Decimal('0'),
            closing_balance=Decimal('0'),
            imported_by=cls.viewer,
        )
        BankTransaction.all_objects.create(
            tenant=cls.tenant,
            bank_statement=statement,
            transaction_date=date(2026, 8, 6),
            amount=Decimal('10347.20'),
            transaction_type='debit',
            description='PPMV',
            currency='EUR',
            match_status='matched',
            matched_journal_entry=cls.manual,
            external_id='btx-ppmv',
        )
        payment = Payment.all_objects.create(
            tenant=cls.tenant,
            payment_number='PAY-JE-1',
            payment_type='outgoing',
            payment_method='bank_transfer',
            status='completed',
            amount=Decimal('50.00'),
            bank_account=bank,
            payment_date=date(2026, 7, 20),
            created_by=cls.viewer,
        )
        cls.payment_je = cls._entry(
            cls.tenant,
            cls.viewer,
            number='202607-PAY',
            description='Uplata',
            status='posted',
            entry_date=date(2026, 7, 20),
            is_auto=True,
            source_content_type=payment_ct,
            source_object_id=payment.pk,
        )
        cls._balanced_lines(cls.payment_je, cls.account, Decimal('50.00'))

        other_je = cls._entry(
            cls.other,
            cls.outsider,
            number='OTHER-1',
            description='Tuđi tenant',
            status='posted',
            entry_date=date(2026, 8, 1),
        )
        cls._balanced_lines(other_je, other_account, Decimal('9.00'))

    @staticmethod
    def _entry(tenant, user, *, number, description, status, entry_date, is_auto=False, **extra):
        return JournalEntry.all_objects.create(
            tenant=tenant,
            entry_number=number,
            entry_date=entry_date,
            description=description,
            status=status,
            is_auto=is_auto,
            created_by=user,
            **extra,
        )

    @staticmethod
    def _balanced_lines(entry, account, amount: Decimal):
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=account,
            debit_amount=amount,
            credit_amount=Decimal('0'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=account,
            debit_amount=Decimal('0'),
            credit_amount=amount,
        )

    def _client(self, user, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def test_unauthenticated_is_401(self):
        client = APIClient()
        client.defaults['HTTP_HOST'] = HOST
        response = client.get('/api/finance/journal-entries/')
        self.assertEqual(response.status_code, 401)

    def test_list_totals_sum_all_lines(self):
        client = self._client(self.viewer)
        response = client.get('/api/finance/journal-entries/')
        self.assertEqual(response.status_code, 200)
        by_number = {row['entry_number']: row for row in response.data['results']}
        multi = by_number['202607-MULTI']
        self.assertEqual(multi['total_debit'], '125.50')
        self.assertEqual(multi['total_credit'], '125.50')
        self.assertEqual(multi['source_type'], 'manual')

    def test_source_types_from_gfk_only(self):
        client = self._client(self.viewer)
        response = client.get('/api/finance/journal-entries/')
        by_number = {row['entry_number']: row for row in response.data['results']}
        self.assertEqual(by_number['202607-INV']['source_type'], 'invoice')
        self.assertEqual(by_number['202607-EXP']['source_type'], 'expense')
        self.assertEqual(by_number['202608-MAN']['source_type'], 'manual')
        self.assertEqual(by_number['202608-ASSET']['source_type'], 'asset')

    def test_bank_match_does_not_change_source_type(self):
        client = self._client(self.viewer)
        response = client.get('/api/finance/journal-entries/', {'search': 'PPMV'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        row = response.data['results'][0]
        self.assertEqual(row['entry_number'], '202608-MAN')
        self.assertEqual(row['source_type'], 'manual')
        self.assertNotIn('has_bank_match', row)
        self.assertNotEqual(row['source_type'], 'bank')

    def test_cross_tenant_entries_are_hidden(self):
        client = self._client(self.viewer)
        response = client.get('/api/finance/journal-entries/')
        numbers = {row['entry_number'] for row in response.data['results']}
        self.assertNotIn('OTHER-1', numbers)

        outsider = self._client(self.outsider, host=OTHER_HOST)
        other = outsider.get('/api/finance/journal-entries/')
        self.assertEqual(other.status_code, 200)
        other_numbers = {row['entry_number'] for row in other.data['results']}
        self.assertEqual(other_numbers, {'OTHER-1'})
        self.assertNotIn('202607-MULTI', other_numbers)

    def test_status_filter_and_pagination(self):
        client = self._client(self.viewer)
        drafts = client.get('/api/finance/journal-entries/', {'status': 'draft'})
        self.assertEqual(drafts.status_code, 200)
        self.assertEqual(drafts.data['count'], 1)
        self.assertEqual(drafts.data['results'][0]['entry_number'], '202608-DRAFT')

        page1 = client.get('/api/finance/journal-entries/', {'page': 1, 'page_size': 2})
        self.assertEqual(page1.status_code, 200)
        self.assertEqual(page1.data['page'], 1)
        self.assertEqual(page1.data['page_size'], 2)
        self.assertEqual(len(page1.data['results']), 2)
        self.assertGreater(page1.data['count'], 2)

        capped = client.get('/api/finance/journal-entries/', {'page_size': 500})
        self.assertEqual(capped.status_code, 200)
        self.assertEqual(capped.data['page_size'], 100)

    def test_invalid_status_is_400(self):
        client = self._client(self.viewer)
        response = client.get('/api/finance/journal-entries/', {'status': 'paid'})
        self.assertEqual(response.status_code, 400)

    def test_cost_center_filter_matches_line_only(self):
        from accounting.models import CostCenter, CostCenterKind

        kitchen = CostCenter.all_objects.create(
            tenant=self.tenant,
            code='110',
            name='Kuhinja',
            kind=CostCenterKind.LOCATION,
        )
        other_cc = CostCenter.all_objects.create(
            tenant=self.tenant,
            code='100',
            name='Restoran',
            kind=CostCenterKind.LOCATION,
        )
        JournalEntryLine.objects.filter(journal_entry=self.multi).update(cost_center=kitchen)
        other_line = JournalEntryLine.objects.filter(journal_entry=self.invoice_je).first()
        if other_line is None:
            JournalEntryLine.objects.create(
                journal_entry=self.invoice_je,
                account=self.account,
                debit_amount=Decimal('10.00'),
                credit_amount=Decimal('0'),
                cost_center=other_cc,
            )
        else:
            other_line.cost_center = other_cc
            other_line.save(update_fields=['cost_center'])

        client = self._client(self.viewer)
        matched = client.get('/api/finance/journal-entries/', {'cost_center': kitchen.pk})
        self.assertEqual(matched.status_code, 200)
        numbers = {row['entry_number'] for row in matched.data['results']}
        self.assertEqual(numbers, {'202607-MULTI'})

        other = client.get('/api/finance/journal-entries/', {'cost_center': other_cc.pk})
        self.assertEqual(other.status_code, 200)
        other_numbers = {row['entry_number'] for row in other.data['results']}
        self.assertNotIn('202607-MULTI', other_numbers)

    def test_search_uses_model_fields_only(self):
        client = self._client(self.viewer)
        by_ref = client.get('/api/finance/journal-entries/', {'search': 'WAUZZZF86RN003268'})
        self.assertEqual(by_ref.status_code, 200)
        self.assertEqual(by_ref.data['count'], 1)
        self.assertEqual(by_ref.data['results'][0]['entry_number'], '202608-MAN')

    def test_detail_unauthenticated_is_401(self):
        client = APIClient()
        client.defaults['HTTP_HOST'] = HOST
        response = client.get(f'/api/finance/journal-entries/{self.multi.pk}/')
        self.assertEqual(response.status_code, 401)

    def test_detail_cross_tenant_is_404(self):
        outsider = self._client(self.outsider, host=OTHER_HOST)
        response = outsider.get(f'/api/finance/journal-entries/{self.multi.pk}/')
        self.assertEqual(response.status_code, 404)

        missing = self._client(self.viewer).get('/api/finance/journal-entries/999999/')
        self.assertEqual(missing.status_code, 404)

    def test_detail_returns_lines_reference_and_source(self):
        client = self._client(self.viewer)
        response = client.get(f'/api/finance/journal-entries/{self.manual.pk}/')
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data['entry_number'], '202608-MAN')
        self.assertEqual(data['reference'], 'WAUZZZF86RN003268')
        self.assertEqual(data['source_type'], 'manual')
        self.assertIsNone(data['source_id'])
        self.assertEqual(data['total_debit'], '10347.20')
        self.assertEqual(data['total_credit'], '10347.20')
        self.assertEqual(len(data['lines']), 2)
        self.assertEqual(data['lines'][0]['account_code'], '1000')
        self.assertEqual(data['lines'][0]['account_name'], 'Banka')
        self.assertNotIn('has_bank_match', data)
        self.assertIn('as_of', data)

        invoice = client.get(f'/api/finance/journal-entries/{self.invoice_je.pk}/')
        self.assertEqual(invoice.status_code, 200)
        self.assertEqual(invoice.data['source_type'], 'invoice')
        self.assertEqual(invoice.data['source_id'], self.invoice.pk)
        self.assertIsNone(data['source_document'])
        self.assertEqual(
            invoice.data['source_document'],
            {
                'direction': 'outgoing',
                'id': self.invoice.pk,
                'label': 'INV-001/2026',
            },
        )

    def test_detail_source_document_expense(self):
        client = self._client(self.viewer)
        response = client.get(f'/api/finance/journal-entries/{self.expense_je.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['source_type'], 'expense')
        self.assertEqual(
            response.data['source_document'],
            {
                'direction': 'incoming',
                'id': self.expense.pk,
                'label': 'T-2026-0001',
            },
        )

    def test_detail_source_document_official_keeps_source_type_other(self):
        client = self._client(self.viewer)
        response = client.get(f'/api/finance/journal-entries/{self.official_je.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['source_type'], 'other')
        self.assertEqual(
            response.data['source_document'],
            {
                'direction': 'official',
                'id': self.official.pk,
                'label': 'UP/I-410-22/26-09/36939',
            },
        )

    def test_detail_source_document_null_for_payment_and_deposit(self):
        client = self._client(self.viewer)
        payment = client.get(f'/api/finance/journal-entries/{self.payment_je.pk}/')
        self.assertEqual(payment.status_code, 200)
        self.assertEqual(payment.data['source_type'], 'other')
        self.assertIsNone(payment.data['source_document'])

        deposit = client.get(f'/api/finance/journal-entries/{self.deposit_je.pk}/')
        self.assertEqual(deposit.status_code, 200)
        self.assertEqual(deposit.data['source_type'], 'other')
        self.assertIsNone(deposit.data['source_document'])

    def test_detail_source_document_null_for_cross_tenant_gfk(self):
        client = self._client(self.viewer)
        response = client.get(f'/api/finance/journal-entries/{self.cross_tenant_source_je.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['source_id'], self.other_official.pk)
        self.assertIsNone(response.data['source_document'])

    def test_detail_source_document_null_for_dangling_gfk(self):
        client = self._client(self.viewer)
        response = client.get(f'/api/finance/journal-entries/{self.dangling_je.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['source_id'], 999999)
        self.assertIsNone(response.data['source_document'])

    def test_detail_bank_match_does_not_change_source_type(self):
        client = self._client(self.viewer)
        response = client.get(f'/api/finance/journal-entries/{self.manual.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['source_type'], 'manual')
        self.assertNotIn('has_bank_match', response.data)

    def test_detail_line_amounts_sum_to_totals(self):
        client = self._client(self.viewer)
        response = client.get(f'/api/finance/journal-entries/{self.multi.pk}/')
        self.assertEqual(response.status_code, 200)
        data = response.data
        debit_sum = sum(Decimal(line['debit']) for line in data['lines'])
        credit_sum = sum(Decimal(line['credit']) for line in data['lines'])
        self.assertEqual(debit_sum, Decimal(data['total_debit']))
        self.assertEqual(credit_sum, Decimal(data['total_credit']))
        self.assertEqual(data['total_debit'], '125.50')
        self.assertEqual(data['total_credit'], '125.50')
        self.assertEqual(len(data['lines']), 3)
        ids = [line['id'] for line in data['lines']]
        self.assertEqual(ids, sorted(ids))
