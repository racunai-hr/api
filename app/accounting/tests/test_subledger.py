"""SubledgerItem lifecycle — open item, partial payment, expense payment, storno."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import SubledgerAllocation, SubledgerItem
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import (
    ensure_default_posting_rules,
    post_document,
    post_invoice_payment,
)
from accounting.services.rrif_import import import_rrif_chart
from expenses.models import Expense, ExpenseCategory
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from payments.models import BankAccount, Payment
from tenants.models import Tenant


class SubledgerPostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='subledger-co', name='Subledger Co')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='subledger-user', password='test')
        cls.customer = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Kupac d.o.o.',
            tax_number='12345678901',
            partner_type='customer',
            status='active',
            address='Ulica 1',
            city='Šibenik',
            postal_code='22000',
        )
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljač d.o.o.',
            tax_number='98765432109',
            partner_type='supplier',
            status='active',
            address='Ulica 2',
            city='Zagreb',
            postal_code='10000',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def _create_invoice(self, **overrides):
        defaults = {
            'tenant': self.tenant,
            'company_to': self.customer,
            'issue_date': date(2026, 5, 15),
            'due_date': date(2026, 5, 30),
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
        return invoice

    def _create_expense(self, **overrides):
        defaults = {
            'tenant': self.tenant,
            'expense_number': f'EXP-SL-{SubledgerItem.objects.count() + 1}',
            'status': 'approved',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('125.00'),
            'tax_amount': Decimal('25.00'),
            'expense_date': date(2026, 4, 15),
            'due_date': date(2026, 4, 30),
            'description': 'Test trošak',
            'created_by': self.user,
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def _create_payment(self, invoice, amount, payment_number='PAY-SL-001'):
        bank_account = BankAccount.all_objects.create(
            tenant=self.tenant,
            account_name='Poslovni EUR',
            bank_name='OTP',
            account_number='HR6124070001100204771',
            iban='HR6124070001100204771',
        )
        return Payment.all_objects.create(
            tenant=self.tenant,
            payment_number=payment_number,
            payment_type='incoming',
            payment_method='bank_transfer',
            status='completed',
            amount=Decimal(str(amount)),
            bank_account=bank_account,
            related_invoice=invoice,
            payment_date=date(2026, 5, 18),
            created_by=self.user,
        )

    def test_invoice_issued_creates_receivable_open_item(self):
        invoice = self._create_invoice()
        post_document(self.tenant, invoice, 'invoice_issued', self.user)

        item = SubledgerItem.all_objects.get(tenant=self.tenant, source_object_id=invoice.pk)
        self.assertEqual(item.direction, 'receivable')
        self.assertEqual(item.partner_id, self.customer.pk)
        self.assertEqual(item.original_amount, Decimal('125.00'))
        self.assertEqual(item.open_amount, Decimal('125.00'))
        self.assertEqual(item.status, 'open')
        self.assertEqual(item.due_date, date(2026, 5, 30))

    def test_expense_approved_creates_payable_open_item(self):
        expense = self._create_expense()
        post_document(self.tenant, expense, 'expense_approved', self.user)

        item = SubledgerItem.all_objects.get(tenant=self.tenant, source_object_id=expense.pk)
        self.assertEqual(item.direction, 'payable')
        self.assertEqual(item.partner_id, self.supplier.pk)
        self.assertEqual(item.original_amount, Decimal('125.00'))
        self.assertEqual(item.open_amount, Decimal('125.00'))
        self.assertEqual(item.status, 'open')

    def test_partial_invoice_payment_updates_open_amount(self):
        invoice = self._create_invoice()
        post_document(self.tenant, invoice, 'invoice_issued', self.user)
        payment = self._create_payment(invoice, '50.00', payment_number='PAY-SL-PARTIAL')
        post_invoice_payment(self.tenant, invoice, payment, self.user)

        item = SubledgerItem.all_objects.get(tenant=self.tenant, source_object_id=invoice.pk)
        self.assertEqual(item.open_amount, Decimal('75.00'))
        self.assertEqual(item.status, 'partial')
        self.assertEqual(
            SubledgerAllocation.all_objects.filter(subledger_item=item).count(),
            1,
        )

    def test_full_invoice_payment_closes_open_item(self):
        invoice = self._create_invoice()
        post_document(self.tenant, invoice, 'invoice_issued', self.user)
        payment = self._create_payment(invoice, '125.00', payment_number='PAY-SL-FULL')
        post_invoice_payment(self.tenant, invoice, payment, self.user)

        item = SubledgerItem.all_objects.get(tenant=self.tenant, source_object_id=invoice.pk)
        self.assertEqual(item.open_amount, Decimal('0'))
        self.assertEqual(item.status, 'closed')
        self.assertIsNotNone(item.closed_at)

    def test_expense_paid_closes_payable_item(self):
        expense = self._create_expense(
            expense_number='EXP-SL-PAID',
            status='paid',
            settlement_method='business_account',
        )
        post_document(self.tenant, expense, 'expense_approved', self.user)
        post_document(self.tenant, expense, 'expense_paid', self.user)

        item = SubledgerItem.all_objects.get(tenant=self.tenant, source_object_id=expense.pk)
        self.assertEqual(item.open_amount, Decimal('0'))
        self.assertEqual(item.status, 'closed')

    def test_storno_document_entry_cancels_open_item(self):
        invoice = self._create_invoice()
        entry = post_document(self.tenant, invoice, 'invoice_issued', self.user)
        item = SubledgerItem.all_objects.get(tenant=self.tenant, source_object_id=invoice.pk)

        entry.reverse(self.user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'cancelled')
        self.assertEqual(item.open_amount, Decimal('0'))

    def test_storno_payment_restores_open_amount(self):
        invoice = self._create_invoice()
        post_document(self.tenant, invoice, 'invoice_issued', self.user)
        payment = self._create_payment(invoice, '50.00', payment_number='PAY-SL-STORNO')
        payment_entry = post_invoice_payment(self.tenant, invoice, payment, self.user)

        payment_entry.reverse(self.user)
        item = SubledgerItem.all_objects.get(tenant=self.tenant, source_object_id=invoice.pk)
        self.assertEqual(item.open_amount, Decimal('125.00'))
        self.assertEqual(item.status, 'open')
        self.assertFalse(
            SubledgerAllocation.all_objects.filter(subledger_item=item).exists(),
        )

    def test_create_subledger_item_is_idempotent(self):
        invoice = self._create_invoice()
        post_document(self.tenant, invoice, 'invoice_issued', self.user)
        post_document(self.tenant, invoice, 'invoice_issued', self.user)

        self.assertEqual(
            SubledgerItem.all_objects.filter(
                tenant=self.tenant,
                source_object_id=invoice.pk,
            ).exclude(status='cancelled').count(),
            1,
        )
