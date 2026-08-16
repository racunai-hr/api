from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from accounting.models import JournalEntry, JournalEntryLine
from accounting.services.analytics import get_or_create_analytic_for_payer
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules, post_document
from accounting.services.rrif_import import import_rrif_chart
from expenses.models import (
    Expense,
    ExpenseCategory,
    ExpensePayer,
    ExpenseSource,
    ReimbursementStatus,
    SettlementMethod,
)
from expenses.tests.partner_helpers import create_supplier_partner
from expenses.parsers.f1_csv import F1CsvParser
from expenses.services.import_service import import_expense_rows
from tenants.models import Tenant

IMPORT_CSV = """\
Naziv poreznog obveznika: FINE STAR d.o.o.
OIB poreznog obveznika: 36619131370
Razdoblje: 01.06.2026. 00:00:00 - 30.06.2026. 23:59:00
Broj računa;Oznaka poslovnog prostora;Oznaka naplatnog uređaja;JIR;OIB izdavatelja računa;Datum i vrijeme izdavanja računa;Datum fiskalizacije;Osnovica 0%;Porez 0%;Osnovica 5%;Porez 5%;Osnovica 13%;Porez 13%;Osnovica 25%;Porez 25%;Ukupni iznos računa;Način plaćanja
20965;BB12;1;jir-20965;81793146560;30.05.2026. 08:19:26;30.05.2026. 08:19:27;;;;;;;3,20;0,80;4,00;K
"""


class PrivateSettlementPostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='settleco', name='Settle Co')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        cls.user = User.objects.create_user(username='settler', password='test')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.supplier = create_supplier_partner(
            tenant=cls.tenant,
            name='Dobavljač',
            tax_number='81793146560',
        )
        cls.payer = ExpensePayer.all_objects.create(
            tenant=cls.tenant,
            name='Ante Vrcan',
            oib='11528564544',
        )

    def _create_expense(self, **kwargs):
        defaults = {
            'tenant': self.tenant,
            'expense_number': 'T-2026-0099',
            'source': ExpenseSource.MANUAL,
            'status': 'paid',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('12.00'),
            'tax_amount': Decimal('2.40'),
            'currency': 'EUR',
            'expense_date': date(2026, 6, 1),
            'description': 'Test trošak',
            'created_by': self.user,
        }
        defaults.update(kwargs)
        return Expense.all_objects.create(**defaults)

    def _paid_credit_account_code(self, expense):
        post_document(self.tenant, expense, 'expense_approved', self.user)
        entry = post_document(self.tenant, expense, 'expense_paid', self.user)
        self.assertIsNotNone(entry)
        credit_line = entry.lines.filter(credit_amount__gt=0).first()
        return credit_line.account.account_code

    def test_private_card_posts_to_2309_with_payer_analytic(self):
        expense = self._create_expense(
            expense_number='T-2026-0101',
            settlement_method=SettlementMethod.PRIVATE_CARD,
            paid_by=self.payer,
            reimbursement_status=ReimbursementStatus.PENDING,
        )
        credit_code = self._paid_credit_account_code(expense)
        self.assertTrue(credit_code.startswith('2309-P'))
        analytic = get_or_create_analytic_for_payer(self.tenant, self.payer)
        self.assertEqual(credit_code, analytic.account_code)

    def test_business_account_posts_to_1000(self):
        expense = self._create_expense(
            expense_number='T-2026-0102',
            settlement_method=SettlementMethod.BUSINESS_ACCOUNT,
        )
        self.assertEqual(self._paid_credit_account_code(expense), '1000')

    def test_company_cash_posts_to_1020(self):
        expense = self._create_expense(
            expense_number='T-2026-0103',
            settlement_method=SettlementMethod.COMPANY_CASH,
        )
        self.assertEqual(self._paid_credit_account_code(expense), '1020')

    def test_empty_settlement_method_skips_expense_paid(self):
        expense = self._create_expense(
            expense_number='T-2026-0104',
            settlement_method='',
        )
        post_document(self.tenant, expense, 'expense_approved', self.user)
        entry = post_document(self.tenant, expense, 'expense_paid', self.user)
        self.assertIsNone(entry)

    def test_private_settlement_without_paid_by_raises_on_save(self):
        from django.core.exceptions import ValidationError

        expense = Expense(
            tenant=self.tenant,
            expense_number='T-2026-0105',
            source=ExpenseSource.MANUAL,
            status='paid',
            settlement_method=SettlementMethod.PRIVATE_CARD,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('4.00'),
            tax_amount=Decimal('0.80'),
            currency='EUR',
            expense_date=date(2026, 6, 1),
            description='Invalid',
            created_by=self.user,
        )
        with self.assertRaises(ValidationError):
            expense.full_clean()


class PrivateSettlementImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='importsettle', name='Import Settle')
        cls.user = User.objects.create_user(username='importsettle', password='test')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.payer = ExpensePayer.all_objects.create(
            tenant=cls.tenant,
            name='Ante Vrcan',
            oib='11528564544',
        )

    def test_f1_import_keeps_payment_method_and_sets_settlement_separately(self):
        parse_result = F1CsvParser().parse(IMPORT_CSV)
        result = import_expense_rows(
            tenant=self.tenant,
            user=self.user,
            rows=parse_result.rows,
            source=ExpenseSource.F1_CSV,
            filename='test.csv',
            dry_run=False,
            status='paid',
            settlement_method=SettlementMethod.PRIVATE_CARD,
            paid_by=self.payer,
        )
        self.assertEqual(result.created, 1)
        expense = Expense.all_objects.get(source=ExpenseSource.F1_CSV)
        self.assertEqual(expense.payment_method, 'card')
        self.assertEqual(expense.settlement_method, SettlementMethod.PRIVATE_CARD)
        self.assertEqual(expense.paid_by, self.payer)
        self.assertEqual(expense.reimbursement_status, ReimbursementStatus.PENDING)


class FixPrivateSettlementCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='fixco', name='Fix Co')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        cls.user = User.objects.create_user(username='fixer', password='test', is_superuser=True)
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.supplier = create_supplier_partner(
            tenant=cls.tenant,
            name='Dobavljač',
            tax_number='81793146560',
        )
        cls.payer = ExpensePayer.all_objects.create(
            tenant=cls.tenant,
            name='Ante Vrcan',
            oib='11528564544',
        )

    def test_fix_private_settlement_reposts_with_2309(self):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-2026-0001',
            source=ExpenseSource.MANUAL,
            payment_method='card',
            settlement_method=SettlementMethod.BUSINESS_ACCOUNT,
            status='paid',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('4.00'),
            tax_amount=Decimal('0.80'),
            currency='EUR',
            expense_date=date(2026, 6, 4),
            receipt_number='21822-BB12-1',
            description='Stari trošak',
            created_by=self.user,
        )
        post_document(self.tenant, expense, 'expense_approved', self.user)
        post_document(self.tenant, expense, 'expense_paid', self.user)

        out = StringIO()
        call_command(
            'fix_private_settlement',
            tenant=self.tenant.slug,
            expense_number='T-2026-0001',
            settlement_method=SettlementMethod.PRIVATE_CARD,
            paid_by_oib='11528564544',
            stdout=out,
        )

        expense.refresh_from_db()
        self.assertEqual(expense.settlement_method, SettlementMethod.PRIVATE_CARD)
        self.assertEqual(expense.paid_by, self.payer)
        self.assertEqual(expense.reimbursement_status, ReimbursementStatus.PENDING)

        paid_entry = JournalEntry.all_objects.filter(
            tenant=self.tenant,
            source_object_id=expense.pk,
            description__startswith='[expense_paid]',
            status='posted',
        ).latest('pk')
        credit_line = JournalEntryLine.objects.filter(
            journal_entry=paid_entry,
            credit_amount__gt=0,
        ).first()
        self.assertTrue(credit_line.account.account_code.startswith('2309-P'))

    def test_fix_private_settlement_dry_run(self):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-2026-0002',
            source=ExpenseSource.MANUAL,
            settlement_method=SettlementMethod.BUSINESS_ACCOUNT,
            status='paid',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('4.00'),
            tax_amount=Decimal('0.80'),
            currency='EUR',
            expense_date=date(2026, 6, 4),
            description='Dry run trošak',
            created_by=self.user,
        )
        post_document(self.tenant, expense, 'expense_approved', self.user)
        post_document(self.tenant, expense, 'expense_paid', self.user)

        out = StringIO()
        call_command(
            'fix_private_settlement',
            tenant=self.tenant.slug,
            expense_number='T-2026-0002',
            settlement_method=SettlementMethod.PRIVATE_CARD,
            paid_by_oib='11528564544',
            dry_run=True,
            stdout=out,
        )
        expense.refresh_from_db()
        self.assertEqual(expense.settlement_method, SettlementMethod.BUSINESS_ACCOUNT)
        self.assertIn('Dry-run', out.getvalue())
