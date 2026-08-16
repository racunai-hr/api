"""Partner aging izvještaj — bucketi i open items lista."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import SubledgerItem
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import (
    ensure_default_posting_rules,
    post_document,
    post_invoice_payment,
)
from accounting.services.reports import (
    aging_bucket_for_days,
    export_subledger_aging_xlsx,
    partner_aging,
    subledger_open_items,
)
from accounting.services.rrif_import import import_rrif_chart
from expenses.models import Expense, ExpenseCategory
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from payments.models import BankAccount, Payment
from tenants.models import Tenant


class SubledgerAgingReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='aging-co', name='Aging Co')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='aging-user', password='test')
        cls.customer = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Kupac aging',
            tax_number='11111111111',
            partner_type='customer',
            status='active',
            address='Ulica 1',
            city='Split',
            postal_code='21000',
        )
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljač aging',
            tax_number='22222222222',
            partner_type='supplier',
            status='active',
            address='Ulica 2',
            city='Zagreb',
            postal_code='10000',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def _create_invoice(self, due_date, **overrides):
        defaults = {
            'tenant': self.tenant,
            'company_to': self.customer,
            'issue_date': date(2026, 1, 15),
            'due_date': due_date,
            'status': 'sent',
            'subtotal': Decimal('100.00'),
            'tax_amount': Decimal('25.00'),
            'total_amount': Decimal('125.00'),
            'created_by': self.user,
        }
        defaults.update(overrides)
        invoice = Invoice.all_objects.create(**defaults)
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Usluga',
            quantity=1,
            unit_price=Decimal('100.00'),
            tax_rate=Decimal('25.00'),
        )
        post_document(self.tenant, invoice, 'invoice_issued', self.user)
        return invoice

    def _create_expense(self, due_date, **overrides):
        defaults = {
            'tenant': self.tenant,
            'expense_number': f'EXP-AG-{SubledgerItem.objects.count() + 1}',
            'status': 'approved',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('80.00'),
            'tax_amount': Decimal('20.00'),
            'expense_date': date(2026, 1, 10),
            'due_date': due_date,
            'description': 'Trošak aging',
            'created_by': self.user,
        }
        defaults.update(overrides)
        expense = Expense.all_objects.create(**defaults)
        post_document(self.tenant, expense, 'expense_approved', self.user)
        return expense

    def test_aging_bucket_boundaries(self):
        self.assertEqual(aging_bucket_for_days(0), 'current')
        self.assertEqual(aging_bucket_for_days(-5), 'current')
        self.assertEqual(aging_bucket_for_days(1), '1_30')
        self.assertEqual(aging_bucket_for_days(30), '1_30')
        self.assertEqual(aging_bucket_for_days(31), '31_60')
        self.assertEqual(aging_bucket_for_days(60), '31_60')
        self.assertEqual(aging_bucket_for_days(61), '61_90')
        self.assertEqual(aging_bucket_for_days(90), '61_90')
        self.assertEqual(aging_bucket_for_days(91), '90_plus')

    def test_partner_aging_groups_open_items_by_bucket(self):
        as_of = date(2026, 6, 30)
        self._create_invoice(due_date=date(2026, 7, 15))  # current
        self._create_invoice(due_date=date(2026, 6, 20))  # 10 days -> 1_30
        self._create_invoice(due_date=date(2026, 5, 1))   # 60 days -> 31_60
        self._create_expense(due_date=date(2026, 3, 1))   # payable 121 days -> 90_plus

        aging = partner_aging(self.tenant, as_of_date=as_of)
        self.assertEqual(aging['as_of_date'], as_of)
        self.assertEqual(len(aging['partners']), 2)

        customer_row = next(r for r in aging['partners'] if r['partner_id'] == self.customer.pk)
        self.assertEqual(customer_row['buckets']['current'], Decimal('125.00'))
        self.assertEqual(customer_row['buckets']['1_30'], Decimal('125.00'))
        self.assertEqual(customer_row['buckets']['31_60'], Decimal('125.00'))
        self.assertEqual(customer_row['total'], Decimal('375.00'))

        supplier_row = next(r for r in aging['partners'] if r['partner_id'] == self.supplier.pk)
        self.assertEqual(supplier_row['buckets']['90_plus'], Decimal('80.00'))

        self.assertEqual(aging['totals']['current'], Decimal('125.00'))
        self.assertEqual(aging['totals']['1_30'], Decimal('125.00'))
        self.assertEqual(aging['totals']['31_60'], Decimal('125.00'))
        self.assertEqual(aging['totals']['90_plus'], Decimal('80.00'))
        self.assertEqual(aging['totals']['total'], Decimal('455.00'))

    def test_direction_filter_limits_report(self):
        as_of = date(2026, 6, 30)
        self._create_invoice(due_date=date(2026, 6, 1))
        self._create_expense(due_date=date(2026, 5, 1))

        ar_aging = partner_aging(self.tenant, direction='receivable', as_of_date=as_of)
        ap_aging = partner_aging(self.tenant, direction='payable', as_of_date=as_of)

        self.assertEqual(len(ar_aging['partners']), 1)
        self.assertEqual(ar_aging['partners'][0]['partner_id'], self.customer.pk)
        self.assertEqual(len(ap_aging['partners']), 1)
        self.assertEqual(ap_aging['partners'][0]['partner_id'], self.supplier.pk)

    def test_partial_payment_reduces_aging_amount(self):
        as_of = date(2026, 6, 30)
        invoice = self._create_invoice(due_date=date(2026, 6, 1))
        bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni EUR',
            bank_name='OTP',
            account_number='HR6124070001100204771',
            iban='HR6124070001100204771',
        )
        payment = Payment.all_objects.create(
            tenant=self.tenant,
            payment_number='PAY-AG-001',
            payment_type='incoming',
            payment_method='bank_transfer',
            status='completed',
            amount=Decimal('25.00'),
            bank_account=bank_account,
            related_invoice=invoice,
            payment_date=date(2026, 6, 10),
            created_by=self.user,
        )
        post_invoice_payment(self.tenant, invoice, payment, self.user)

        aging = partner_aging(self.tenant, direction='receivable', as_of_date=as_of)
        customer_row = aging['partners'][0]
        self.assertEqual(customer_row['buckets']['1_30'], Decimal('100.00'))
        self.assertEqual(customer_row['total'], Decimal('100.00'))

    def test_closed_items_excluded_from_open_list(self):
        as_of = date(2026, 6, 30)
        invoice = self._create_invoice(due_date=date(2026, 6, 1))
        bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni EUR',
            bank_name='OTP',
            account_number='HR6124070001100204772',
            iban='HR6124070001100204772',
        )
        payment = Payment.all_objects.create(
            tenant=self.tenant,
            payment_number='PAY-AG-FULL',
            payment_type='incoming',
            payment_method='bank_transfer',
            status='completed',
            amount=Decimal('125.00'),
            bank_account=bank_account,
            related_invoice=invoice,
            payment_date=date(2026, 6, 10),
            created_by=self.user,
        )
        post_invoice_payment(self.tenant, invoice, payment, self.user)

        self.assertEqual(subledger_open_items(self.tenant, as_of_date=as_of), [])
        self.assertEqual(partner_aging(self.tenant, as_of_date=as_of)['totals']['total'], Decimal('0'))

    def test_export_subledger_aging_xlsx(self):
        as_of = date(2026, 6, 30)
        self._create_invoice(due_date=date(2026, 6, 1))
        content = export_subledger_aging_xlsx(self.tenant, as_of_date=as_of)
        self.assertTrue(content[:2] == b'PK')
