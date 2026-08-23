"""ExpenseLine: persistence child of Expense; DB constraints; tenant from parent."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from expenses.models import Expense, ExpenseCategory, ExpenseLine, VehicleLineKind
from expenses.tests.partner_helpers import create_supplier_partner
from tenants.models import Tenant


class ExpenseLineConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='expline', name='Exp Line')
        cls.other = Tenant.objects.create(slug='expline2', name='Other')
        cls.user = User.objects.create_user(username='expline', password='test')
        cls.category = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Registracija',
            code='vehicle_registration',
        )
        cls.supplier = create_supplier_partner(
            tenant=cls.tenant,
            name='STP',
            tax_number='12345678901',
        )
        cls.expense = Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0100',
            status='draft',
            category=cls.category,
            supplier=cls.supplier,
            amount=Decimal('329.21'),
            tax_amount=Decimal('6.08'),
            currency='EUR',
            expense_date='2026-03-08',
            receipt_number='STP-1',
            description='Registracija',
            created_by=cls.user,
        )
        cls.other_expense = Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0101',
            status='draft',
            category=cls.category,
            supplier=cls.supplier,
            amount=Decimal('10.00'),
            tax_amount=Decimal('0.00'),
            currency='EUR',
            expense_date='2026-03-08',
            description='Drugi trošak',
            created_by=cls.user,
        )

    def _line(self, **overrides):
        values = {
            'expense': self.expense,
            'position': 1,
            'description': 'Tehnički pregled',
            'net_amount': Decimal('18.69'),
            'vat_amount': Decimal('4.67'),
            'gross_amount': Decimal('23.36'),
        }
        values.update(overrides)
        return ExpenseLine.all_objects.create(**values)

    def test_expense_may_have_zero_lines(self):
        self.assertEqual(self.expense.lines.count(), 0)

    def test_line_totals_need_not_match_header(self):
        line = self._line()
        self.assertEqual(line.gross_amount, Decimal('23.36'))
        self.assertEqual(self.expense.amount, Decimal('329.21'))

    def test_money_fields_use_erp_decimal_standard(self):
        for name in ('net_amount', 'vat_amount', 'gross_amount'):
            field = ExpenseLine._meta.get_field(name)
            self.assertEqual(field.max_digits, 15)
            self.assertEqual(field.decimal_places, 2)

    def test_save_sets_tenant_from_expense(self):
        line = self._line()
        self.assertEqual(line.tenant_id, self.expense.tenant_id)

    def test_save_overwrites_foreign_tenant_input(self):
        line = self._line(tenant=self.other)
        self.assertEqual(line.tenant_id, self.expense.tenant_id)

    def test_clean_rejects_tenant_mismatch(self):
        line = ExpenseLine(
            tenant=self.other,
            expense=self.expense,
            position=1,
            description='Tehnički pregled',
            net_amount=Decimal('18.69'),
            vat_amount=Decimal('4.67'),
            gross_amount=Decimal('23.36'),
        )
        with self.assertRaises(ValidationError) as ctx:
            line.full_clean()
        self.assertIn('tenant', ctx.exception.message_dict)

    def test_duplicate_position_rejected_on_same_expense(self):
        self._line(position=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._line(position=1, description='Druga stavka')

    def test_same_position_allowed_on_different_expenses(self):
        self._line(position=1)
        other = self._line(
            expense=self.other_expense,
            position=1,
            description='Druga isprava',
            net_amount=Decimal('10.00'),
            vat_amount=Decimal('0.00'),
            gross_amount=Decimal('10.00'),
        )
        self.assertEqual(other.position, 1)

    def test_position_zero_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExpenseLine.all_objects.create(
                expense=self.expense,
                position=0,
                description='Tehnički pregled',
                net_amount=Decimal('18.69'),
                vat_amount=Decimal('4.67'),
                gross_amount=Decimal('23.36'),
            )

    def test_negative_amount_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._line(
                net_amount=Decimal('-1.00'),
                vat_amount=Decimal('0.00'),
                gross_amount=Decimal('-1.00'),
            )

    def test_gross_must_equal_net_plus_vat(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._line(gross_amount=Decimal('24.00'))

    def test_zero_vat_line_allowed(self):
        line = self._line(
            description='Naknada za javne ceste',
            net_amount=Decimal('49.66'),
            vat_amount=Decimal('0.00'),
            gross_amount=Decimal('49.66'),
            vehicle_line_kind=VehicleLineKind.ROAD_FEE_ANNUAL,
        )
        self.assertEqual(line.vat_amount, Decimal('0.00'))

    def test_empty_description_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._line(description='')

    def test_save_normalizes_empty_kind_to_null(self):
        line = self._line(vehicle_line_kind='')
        line.refresh_from_db()
        self.assertIsNone(line.vehicle_line_kind)

    def test_empty_kind_rejected_when_bypassing_save(self):
        line = self._line()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExpenseLine.all_objects.filter(pk=line.pk).update(vehicle_line_kind='')

    def test_null_kind_is_not_unclassified(self):
        line = self._line()
        self.assertIsNone(line.vehicle_line_kind)
        classified = self._line(
            position=2,
            description='Nepoznato',
            vehicle_line_kind=VehicleLineKind.UNCLASSIFIED,
        )
        self.assertEqual(classified.vehicle_line_kind, VehicleLineKind.UNCLASSIFIED)

    def test_ordering_by_position(self):
        self._line(position=2, description='Druga')
        self._line(position=1, description='Prva')
        self.assertEqual(
            list(self.expense.lines.values_list('description', flat=True)),
            ['Prva', 'Druga'],
        )
