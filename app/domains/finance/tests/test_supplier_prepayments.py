"""Supplier prepayment engine: accounts, allocation, VAT period, fiscal period."""

from __future__ import annotations

import domains.finance.services.supplier_prepayments as prepayment_service
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import (
    FiscalPeriod,
    JournalEntry,
    VATLedgerEntry,
    VATPeriod,
    VATReturn,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules, resolve_account
from accounting.services.rrif_import import import_rrif_chart
from banking.models import BankStatement, BankTransaction
from domains.finance.services.expenses import approve_expense_for_posting
from domains.finance.services.payable_documents import ExpensePayableDocument
from domains.finance.services.posting_memory import apply_line_accounts
from domains.finance.services.subledger import get_subledger_item_for_source
from domains.finance.services.supplier_prepayments import (
    apply_supplier_prepayment,
    post_supplier_prepayment,
    resolve_supplier_prepayment_account,
)
from expenses.models import Expense, ExpenseCategory, ExpenseLine
from fiscal_gateway.services.inbound_expense import create_inbound_expense_from_ubl
from fiscal_gateway.services.ubl_expense_facts import backfill_expense_ubl_facts
from partners.models import Partner
from payments.models import BankAccount
from super_integration.models import SuperDocumentLink
from tenants.models import Tenant


class SupplierPrepaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='prepay', name='Prepay')
        cls.other_tenant = Tenant.objects.create(slug='prepay-other', name='Other')
        provision_tenant_chart(cls.tenant)
        provision_tenant_chart(cls.other_tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_superuser('prepay-owner', 'prepay@test.hr', 'pass')
        cls.partner = cls._partner(cls.tenant, 'FINA', '11111111111')
        cls.other_partner = cls._partner(cls.tenant, 'Drugi', '22222222222')
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
            statement_number='ST-PREPAY',
            statement_date=date(2026, 9, 30),
            opening_balance=Decimal('0'),
            closing_balance=Decimal('5000'),
            status='imported',
            imported_by=cls.user,
        )
        cls.expense_account = resolve_account(cls.tenant, '4120')

    @staticmethod
    def _partner(tenant, name, oib):
        return Partner.all_objects.create(
            tenant=tenant,
            name=name,
            partner_type='supplier',
            status='active',
            tax_number=oib,
            address='Ulica',
            city='Zagreb',
            postal_code='10000',
            country_code='HR',
        )

    def _debit(self, amount, *, when=date(2026, 9, 1), reference='HR00'):
        return BankTransaction.all_objects.create(
            tenant=self.tenant,
            bank_statement=self.statement,
            transaction_date=when,
            amount=Decimal(amount),
            currency='EUR',
            transaction_type='debit',
            description='Predujam',
            reference=reference,
            counterparty_name='FINA',
            match_status='unmatched',
        )

    def _expense(self, number, amount, tax='0.00', *, partner=None, when=date(2026, 9, 30)):
        gross = Decimal(amount)
        tax_amount = Decimal(tax)
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            supplier=partner or self.partner,
            category=self.category,
            expense_number=number,
            expense_date=when,
            amount=gross,
            tax_amount=tax_amount,
            currency='EUR',
            status='draft',
            description=number,
            created_by=self.user,
        )
        ExpenseLine.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            position=1,
            description=number,
            net_amount=gross - tax_amount,
            vat_amount=tax_amount,
            gross_amount=gross,
        )
        return expense

    def _approve(self, expense):
        apply_line_accounts(expense, {1: self.expense_account.pk})
        approve_expense_for_posting(tenant=self.tenant, expense_id=expense.pk, user=self.user)
        expense.refresh_from_db()
        return expense

    def _post(self, bank_tx, nature, treatment=''):
        return post_supplier_prepayment(
            tenant=self.tenant,
            user=self.user,
            bank_transaction_id=bank_tx.pk,
            partner_id=self.partner.pk,
            purchase_nature=nature,
            vat_treatment=treatment,
            idempotency_key=f'post-{bank_tx.pk}',
        )

    def test_service_without_vat_posts_1251_and_closes_payable(self):
        bank_tx = self._debit('132.72')
        prepayment = self._post(bank_tx, 'service', 'no_vat')
        entry = prepayment.posting_journal_entry
        debit = entry.lines.get(debit_amount__gt=0)
        credit = entry.lines.get(credit_amount__gt=0)
        self.assertEqual(debit.account.account_code, '1251')
        self.assertEqual(credit.account.account_code, '1000')
        self.assertFalse(entry.lines.filter(account__account_code='1400').exists())
        bank_tx.refresh_from_db()
        self.assertEqual(bank_tx.matched_journal_entry_id, entry.pk)

        expense = self._approve(self._expense('A-1', '132.72'))
        document_entry = get_subledger_item_for_source(self.tenant, expense).journal_entry
        self.assertFalse(document_entry.lines.filter(account__account_code='1400').exists())
        apply_supplier_prepayment(
            prepayment=prepayment,
            payable_document=ExpensePayableDocument(expense),
            amount=Decimal('132.72'),
            user=self.user,
            idempotency_key='apply-a',
        )
        expense.refresh_from_db()
        item = get_subledger_item_for_source(self.tenant, expense)
        self.assertEqual(expense.status, 'paid')
        self.assertEqual(item.status, 'closed')
        self.assertEqual(item.open_amount, Decimal('0.00'))
        prepayment.refresh_from_db()
        self.assertEqual(prepayment.status, 'applied')
        self.assertFalse(
            JournalEntry.all_objects.filter(
                tenant=self.tenant,
                description__startswith='[expense_paid]',
                source_object_id=expense.pk,
            ).exists()
        )

    def test_service_with_vat_keeps_input_vat_on_the_document_only(self):
        bank_tx = self._debit('125.00', reference='VAT')
        prepayment = self._post(bank_tx, 'service', 'vat')
        self.assertEqual(
            prepayment.posting_journal_entry.lines.get(debit_amount__gt=0).account.account_code,
            '1250',
        )
        self.assertFalse(
            prepayment.posting_journal_entry.lines.filter(account__account_code='1400').exists()
        )
        expense = self._approve(self._expense('B-1', '125.00', '25.00'))
        document_entry = get_subledger_item_for_source(self.tenant, expense).journal_entry
        self.assertTrue(document_entry.lines.filter(account__account_code='1400').exists())
        apply_supplier_prepayment(
            prepayment=prepayment,
            payable_document=ExpensePayableDocument(expense),
            amount=Decimal('125.00'),
            user=self.user,
            idempotency_key='apply-b',
        )

    def test_leaf_natures_resolve_distinct_accounts(self):
        expected = {
            ('goods_for_resale', ''): '670',
            ('material', ''): '370',
            ('tools_and_transport', ''): '0361',
            ('intangible_software', ''): '0154',
        }
        for (nature, treatment), code in expected.items():
            account = resolve_supplier_prepayment_account(
                self.tenant,
                purchase_nature=nature,
                vat_treatment=treatment,
            )
            self.assertEqual(account.account_code, code)
        self.assertNotEqual(
            resolve_supplier_prepayment_account(
                self.tenant, purchase_nature='tools_and_transport'
            ).account_code,
            '0360',
        )
        self.assertNotEqual(
            resolve_supplier_prepayment_account(
                self.tenant, purchase_nature='intangible_software'
            ).account_code,
            '0156',
        )
        with self.assertRaises(ValidationError):
            resolve_supplier_prepayment_account(self.tenant, purchase_nature='fixed_asset')
        with self.assertRaises(ValidationError):
            resolve_supplier_prepayment_account(self.tenant, purchase_nature='intangible_asset')

    def test_partial_and_many_to_many_allocations(self):
        first = self._post(self._debit('1000.00', reference='A'), 'material')
        second = self._post(self._debit('400.00', reference='B'), 'material')
        first_expense = self._approve(self._expense('R1', '1000.00'))
        second_expense = self._approve(self._expense('R2', '200.00'))
        apply_supplier_prepayment(
            prepayment=first,
            payable_document=ExpensePayableDocument(first_expense),
            amount=Decimal('500.00'),
            user=self.user,
            idempotency_key='nm-1',
        )
        apply_supplier_prepayment(
            prepayment=second,
            payable_document=ExpensePayableDocument(first_expense),
            amount=Decimal('100.00'),
            user=self.user,
            idempotency_key='nm-2',
        )
        apply_supplier_prepayment(
            prepayment=second,
            payable_document=ExpensePayableDocument(second_expense),
            amount=Decimal('200.00'),
            user=self.user,
            idempotency_key='nm-3',
        )
        first_item = get_subledger_item_for_source(self.tenant, first_expense)
        second_item = get_subledger_item_for_source(self.tenant, second_expense)
        self.assertEqual(first_item.open_amount, Decimal('400.00'))
        self.assertEqual(second_item.status, 'closed')
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.applied_amount, Decimal('500.00'))
        self.assertEqual(second.applied_amount, Decimal('300.00'))

    def test_repeat_call_does_not_create_another_entry_or_allocation(self):
        bank_tx = self._debit('80.00', reference='IDEMP')
        first = self._post(bank_tx, 'service', 'no_vat')
        second = self._post(bank_tx, 'service', 'no_vat')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            JournalEntry.all_objects.filter(description__contains=f'btx-{bank_tx.pk}').count(),
            1,
        )
        expense = self._approve(self._expense('ID-1', '80.00'))
        document = ExpensePayableDocument(expense)
        apply_supplier_prepayment(
            prepayment=first,
            payable_document=document,
            amount=Decimal('80.00'),
            user=self.user,
            idempotency_key='same-key',
        )
        apply_supplier_prepayment(
            prepayment=first,
            payable_document=document,
            amount=Decimal('80.00'),
            user=self.user,
            idempotency_key='same-key',
        )
        self.assertEqual(first.applications.count(), 1)
        self.assertEqual(
            JournalEntry.all_objects.filter(description__contains='supplier_prepayment_application').count(),
            1,
        )

    def test_rejects_other_partner_other_tenant_and_too_much(self):
        prepayment = self._post(self._debit('50.00', reference='GUARD'), 'service', 'no_vat')
        other = self._approve(self._expense('OTHER', '50.00', partner=self.other_partner))
        with self.assertRaises(ValidationError):
            apply_supplier_prepayment(
                prepayment=prepayment,
                payable_document=ExpensePayableDocument(other),
                amount=Decimal('50.00'),
                user=self.user,
                idempotency_key='other-partner',
            )
        foreign = Expense.all_objects.create(
            tenant=self.other_tenant,
            supplier=self._partner(self.other_tenant, 'Tudji', '33333333333'),
            category=ExpenseCategory.all_objects.create(tenant=self.other_tenant, name='Ostalo'),
            expense_number='FOREIGN',
            expense_date=date(2026, 9, 30),
            amount=Decimal('50.00'),
            tax_amount=Decimal('0.00'),
            currency='EUR',
            status='approved',
            description='Tudji',
            created_by=self.user,
        )
        with self.assertRaises(ValidationError):
            apply_supplier_prepayment(
                prepayment=prepayment,
                payable_document=ExpensePayableDocument(foreign),
                amount=Decimal('50.00'),
                user=self.user,
                idempotency_key='other-tenant',
            )
        own = self._approve(self._expense('MUCH', '50.00'))
        with self.assertRaises(ValidationError):
            apply_supplier_prepayment(
                prepayment=prepayment,
                payable_document=ExpensePayableDocument(own),
                amount=Decimal('80.00'),
                user=self.user,
                idempotency_key='too-much',
            )

    def test_submitted_vat_period_does_not_block_and_does_not_write_ledger(self):
        VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=9, status='submitted')
        ledger_before = VATLedgerEntry.all_objects.filter(tenant=self.tenant).count()
        returns_before = list(VATReturn.all_objects.filter(tenant=self.tenant).values_list('pk', 'status'))
        bank_tx = self._debit('40.00', reference='VATLOCK')
        prepayment = self._post(bank_tx, 'service', 'no_vat')
        self.assertEqual(prepayment.posting_journal_entry.status, 'posted')
        bank_tx.refresh_from_db()
        self.assertEqual(bank_tx.match_status, 'matched')
        expense = self._approve(self._expense('VAT-1', '40.00'))
        apply_supplier_prepayment(
            prepayment=prepayment,
            payable_document=ExpensePayableDocument(expense),
            amount=Decimal('40.00'),
            user=self.user,
            idempotency_key='vat-open-fiscal',
        )
        item = get_subledger_item_for_source(self.tenant, expense)
        self.assertEqual(item.status, 'closed')
        self.assertEqual(VATLedgerEntry.all_objects.filter(tenant=self.tenant).count(), ledger_before)
        self.assertEqual(
            list(VATReturn.all_objects.filter(tenant=self.tenant).values_list('pk', 'status')),
            returns_before,
        )

    def test_closed_fiscal_period_rejects_posting_and_application(self):
        FiscalPeriod.all_objects.create(tenant=self.tenant, year=2026, month=9, status='closed')
        with self.assertRaises(ValidationError):
            self._post(self._debit('10.00', reference='CLOSED'), 'service', 'no_vat')
        FiscalPeriod.all_objects.filter(tenant=self.tenant, year=2026, month=9).update(status='open')
        prepayment = self._post(self._debit('10.00', reference='THEN-CLOSED'), 'service', 'no_vat')
        expense = self._approve(self._expense('CLOSED-AP', '10.00'))
        FiscalPeriod.all_objects.filter(tenant=self.tenant, year=2026, month=9).update(status='closed')
        with self.assertRaises(ValidationError):
            apply_supplier_prepayment(
                prepayment=prepayment,
                payable_document=ExpensePayableDocument(expense),
                amount=Decimal('10.00'),
                user=self.user,
                idempotency_key='closed-fiscal',
            )

    def test_engine_file_stays_out_of_tax_and_document_types(self):
        text = Path(prepayment_service.__file__).read_text(encoding='utf-8')
        for forbidden in (
            'Expense',
            '1251',
            '1250',
            '1400',
            '2201',
            '4650',
            '1000',
            'lock_open_vat_period_for_source_mutation',
            'generate_vat_ledger',
            'rebuild_vat_ledger',
            'rebuild_period_ledger',
            'create_period_draft',
        ):
            self.assertNotIn(forbidden, text)

    def test_ubl_prepaid_is_stored_and_does_not_shrink_the_gross_amount(self):
        xml = """
        <Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
                 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
                 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
          <cbc:ID>198018-3-32-0926</cbc:ID>
          <cbc:IssueDate>2026-09-30</cbc:IssueDate>
          <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
          <cac:AccountingSupplierParty><cac:Party>
            <cbc:EndpointID>85821130368</cbc:EndpointID>
            <cac:PartyName><cbc:Name>Financijska agencija</cbc:Name></cac:PartyName>
          </cac:Party></cac:AccountingSupplierParty>
          <cac:TaxTotal>
            <cbc:TaxAmount currencyID="EUR">0.00</cbc:TaxAmount>
            <cac:TaxSubtotal>
              <cbc:TaxableAmount currencyID="EUR">132.72</cbc:TaxableAmount>
              <cbc:TaxAmount currencyID="EUR">0.00</cbc:TaxAmount>
              <cac:TaxCategory>
                <cbc:ID>E</cbc:ID>
                <cbc:Percent>0</cbc:Percent>
                <cbc:TaxExemptionReason>Oslobodena placanja PDV-a po cl. 40 Zakona o PDV-u</cbc:TaxExemptionReason>
                <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
              </cac:TaxCategory>
            </cac:TaxSubtotal>
          </cac:TaxTotal>
          <cac:LegalMonetaryTotal>
            <cbc:TaxExclusiveAmount currencyID="EUR">132.72</cbc:TaxExclusiveAmount>
            <cbc:TaxInclusiveAmount currencyID="EUR">132.72</cbc:TaxInclusiveAmount>
            <cbc:PrepaidAmount currencyID="EUR">132.72</cbc:PrepaidAmount>
            <cbc:PayableAmount currencyID="EUR">0.00</cbc:PayableAmount>
          </cac:LegalMonetaryTotal>
        </Invoice>
        """
        draft = create_inbound_expense_from_ubl(
            tenant=self.tenant,
            ubl_xml=xml,
            number_prefix='ERAC',
        )
        expense = draft.expense
        self.assertEqual(expense.amount, Decimal('132.72'))
        self.assertEqual(expense.ubl_payable_amount, Decimal('0.00'))
        self.assertEqual(expense.ubl_prepaid_amount, Decimal('132.72'))
        self.assertEqual(expense.ubl_tax_category, 'E')
        self.assertEqual(expense.ubl_tax_exemption_reason_code, '')
        self.assertIn('cl. 40', expense.ubl_tax_exemption_reason_text)
        expense.ubl_tax_category = ''
        expense.ubl_tax_exemption_reason_text = ''
        expense.save(update_fields=['ubl_tax_category', 'ubl_tax_exemption_reason_text', 'updated_at'])
        content_type = ContentType.objects.get_for_model(Expense)
        SuperDocumentLink.all_objects.create(
            tenant=self.tenant,
            direction=SuperDocumentLink.DIRECTION_INBOUND,
            super_guid='guid-363',
            ubl_xml=xml,
            content_type=content_type,
            object_id=expense.pk,
        )
        self.assertTrue(backfill_expense_ubl_facts(expense))
        expense.refresh_from_db()
        self.assertEqual(expense.ubl_tax_category, 'E')
        self.assertIn('cl. 40', expense.ubl_tax_exemption_reason_text)

    def test_mixed_tax_categories_are_not_guessed(self):
        from fiscal_gateway.services.ubl_expense_facts import tax_facts_from_summary
        from ubl.parser.invoice import ParsedTaxSummaryRow

        facts = tax_facts_from_summary([
            ParsedTaxSummaryRow(Decimal('0'), Decimal('10'), Decimal('0'), 'E', None, 'cl. 40'),
            ParsedTaxSummaryRow(Decimal('25'), Decimal('10'), Decimal('2.50'), 'S', None, None),
        ])
        self.assertEqual(facts['ubl_tax_category'], '')
        self.assertEqual(facts['ubl_tax_exemption_reason_code'], '')
        self.assertEqual(facts['ubl_tax_exemption_reason_text'], '')
