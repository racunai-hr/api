"""ExpenseCategory.code: NULL (not ""), tenant-scoped unique when set."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from expenses.models import ExpenseCategory
from tenants.models import Tenant


class ExpenseCategoryCodeConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='catcode', name='Cat Code')
        cls.other = Tenant.objects.create(slug='catcode2', name='Other')

    def test_save_normalizes_empty_string_to_null(self):
        category = ExpenseCategory.all_objects.create(
            tenant=self.tenant,
            name='Legacy',
            code='',
        )
        category.refresh_from_db()
        self.assertIsNone(category.code)

    def test_empty_string_rejected_when_bypassing_save(self):
        category = ExpenseCategory.all_objects.create(tenant=self.tenant, name='Legacy')
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExpenseCategory.all_objects.filter(pk=category.pk).update(code='')

    def test_duplicate_code_rejected_within_tenant(self):
        ExpenseCategory.all_objects.create(
            tenant=self.tenant,
            name='Gorivo',
            code='vehicle_fuel',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExpenseCategory.all_objects.create(
                tenant=self.tenant,
                name='Gorivo duplikat',
                code='vehicle_fuel',
            )

    def test_same_code_allowed_on_different_tenants(self):
        ExpenseCategory.all_objects.create(
            tenant=self.tenant,
            name='Gorivo',
            code='vehicle_fuel',
        )
        ExpenseCategory.all_objects.create(
            tenant=self.other,
            name='Gorivo',
            code='vehicle_fuel',
        )
        self.assertEqual(
            ExpenseCategory.all_objects.filter(code='vehicle_fuel').count(),
            2,
        )

    def test_multiple_null_codes_allowed_per_tenant(self):
        ExpenseCategory.all_objects.create(tenant=self.tenant, name='Hosting i infrastruktura')
        ExpenseCategory.all_objects.create(tenant=self.tenant, name='Ostalo')
        self.assertEqual(
            ExpenseCategory.all_objects.filter(tenant=self.tenant, code__isnull=True).count(),
            2,
        )

    def test_name_change_does_not_change_code_identity(self):
        first, created = ExpenseCategory.all_objects.update_or_create(
            tenant=self.tenant,
            code='vehicle_fuel',
            defaults={'name': 'Gorivo'},
        )
        self.assertTrue(created)
        second, created = ExpenseCategory.all_objects.update_or_create(
            tenant=self.tenant,
            code='vehicle_fuel',
            defaults={'name': 'Gorivo (preimenovano)'},
        )
        self.assertFalse(created)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.name, 'Gorivo (preimenovano)')
        self.assertEqual(
            ExpenseCategory.all_objects.filter(tenant=self.tenant, code='vehicle_fuel').count(),
            1,
        )
