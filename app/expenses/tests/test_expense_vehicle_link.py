"""Expense.vehicle — tenant guard, PROTECT, no posting-profile side effects."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntry, Vehicle
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules, post_document
from accounting.services.rrif_import import import_rrif_chart
from expenses.models import Expense, ExpenseCategory, ExpensePostingProfile
from expenses.tests.partner_helpers import create_supplier_partner
from tenants.models import Tenant, TenantMembership


class ExpenseVehicleLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.tenant = Tenant.objects.create(slug='exp-vehicle', name='Exp Vehicle')
        cls.other = Tenant.objects.create(slug='exp-vehicle-2', name='Other')
        cls.user = User.objects.create_user(username='exp-vehicle', password='test')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.supplier = create_supplier_partner(tenant=cls.tenant, name='CVH', tax_number='12345678901')
        cls.vehicle = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='VW Golf',
            vin='WVWZZZCD8PW153457',
        )
        cls.other_vehicle = Vehicle.all_objects.create(
            tenant=cls.other,
            name='Tudje vozilo',
            vin='WVGZZZC1ZPY022544',
        )

    def _expense(self, **overrides):
        values = {
            'tenant': self.tenant,
            'expense_number': 'T-2026-0200',
            'status': 'draft',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('82.95'),
            'tax_amount': Decimal('16.59'),
            'currency': 'EUR',
            'expense_date': date(2026, 7, 6),
            'description': 'CVH tehnički',
            'created_by': self.user,
        }
        values.update(overrides)
        return Expense.all_objects.create(**values)

    def test_vehicle_is_optional(self):
        expense = self._expense()
        self.assertIsNone(expense.vehicle_id)

    def test_same_tenant_vehicle_is_valid(self):
        expense = self._expense(vehicle=self.vehicle)
        expense.full_clean()
        self.assertEqual(expense.vehicle_id, self.vehicle.pk)

    def test_full_clean_rejects_cross_tenant_vehicle(self):
        expense = self._expense()
        expense.vehicle = self.other_vehicle
        with self.assertRaises(ValidationError) as ctx:
            expense.full_clean()
        self.assertIn('vehicle', ctx.exception.message_dict)

    def test_protect_blocks_delete_when_expense_is_linked(self):
        self._expense(vehicle=self.vehicle)
        with self.assertRaises(ProtectedError):
            self.vehicle.delete()

    def test_approved_expense_may_change_vehicle(self):
        expense = self._expense(status='approved', vehicle=None)
        expense.vehicle = self.vehicle
        expense.save()
        expense.refresh_from_db()
        self.assertEqual(expense.vehicle_id, self.vehicle.pk)
        self.assertEqual(expense.posting_profile, ExpensePostingProfile.OPEX)


class ExpenseVehiclePostingIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='exp-veh-post', name='Exp Veh Post')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='exp-veh-post', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='owner')
        cls.account_4120 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4120')
        cls.category = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Ostalo',
            default_account=cls.account_4120,
        )
        cls.supplier = create_supplier_partner(tenant=cls.tenant, name='CVH', tax_number='11111111119')
        cls.vehicle = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='VW Golf',
            vin='WVWZZZCD8PW153457',
        )

    def test_opex_with_vehicle_still_posts_4120(self):
        expense = Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-VEH-4120',
            status='approved',
            category=self.category,
            supplier=self.supplier,
            amount=Decimal('100.00'),
            tax_amount=Decimal('0.00'),
            currency='EUR',
            expense_date=date(2026, 7, 6),
            description='CVH s vozilom',
            created_by=self.user,
            posting_profile=ExpensePostingProfile.OPEX,
            vehicle=self.vehicle,
        )
        self.assertEqual(expense.posting_profile, ExpensePostingProfile.OPEX)

        entry = post_document(self.tenant, expense, 'expense_approved', self.user)
        self.assertIsNotNone(entry)
        ct = ContentType.objects.get_for_model(Expense)
        posted = JournalEntry.all_objects.get(
            tenant=self.tenant,
            source_content_type=ct,
            source_object_id=expense.pk,
            description__startswith='[expense_approved]',
            status='posted',
        )
        lines = list(posted.lines.select_related('account').order_by('id'))
        debit = next(line for line in lines if line.debit_amount > 0)
        credit = next(line for line in lines if line.credit_amount > 0)
        self.assertEqual(debit.account.account_code.split('-', 1)[0], '4120')
        self.assertEqual(credit.account.account_code.split('-', 1)[0], '2201')
        self.assertEqual(debit.debit_amount, Decimal('100.00'))

        expense.refresh_from_db()
        self.assertEqual(expense.posting_profile, ExpensePostingProfile.OPEX)
        self.assertEqual(expense.vehicle_id, self.vehicle.pk)
