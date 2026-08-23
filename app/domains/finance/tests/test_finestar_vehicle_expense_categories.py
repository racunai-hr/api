"""FineStar vehicle expense kinds: classification seed only, default_account NULL."""

from django.test import TestCase

from expenses.models import ExpenseCategory
from tenants.management.commands.provision_finestar import (
    FINE_STAR_EXPENSE_CATEGORIES,
    FINE_STAR_VEHICLE_EXPENSE_CATEGORIES,
    apply_finestar_expense_categories,
    apply_finestar_vehicle_expense_categories,
)
from tenants.models import Tenant


class FineStarVehicleExpenseCategorySeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='fsveh', name='FineStar vehicle')
        cls.other = Tenant.objects.create(slug='fsveh2', name='Other vehicle')

    def test_vehicle_seed_creates_fifteen_codes_with_null_account(self):
        codes = apply_finestar_vehicle_expense_categories(self.tenant)
        expected = [code for code, _name in FINE_STAR_VEHICLE_EXPENSE_CATEGORIES]
        self.assertEqual(codes, expected)
        self.assertEqual(len(expected), 15)

        rows = list(ExpenseCategory.all_objects.filter(tenant=self.tenant, code__isnull=False))
        self.assertEqual(len(rows), 15)
        by_code = {row.code: row for row in rows}
        self.assertEqual(set(by_code), set(expected))
        for code, name in FINE_STAR_VEHICLE_EXPENSE_CATEGORIES:
            row = by_code[code]
            self.assertEqual(row.name, name)
            self.assertIsNone(row.default_account_id)
            self.assertTrue(row.is_active)

    def test_vehicle_seed_is_idempotent_and_keyed_by_code_not_name(self):
        apply_finestar_vehicle_expense_categories(self.tenant)
        fuel = ExpenseCategory.all_objects.get(tenant=self.tenant, code='vehicle_fuel')
        fuel.name = 'Gorivo (ručno)'
        fuel.save(update_fields=['name'])

        apply_finestar_vehicle_expense_categories(self.tenant)
        apply_finestar_vehicle_expense_categories(self.tenant)

        rows = ExpenseCategory.all_objects.filter(tenant=self.tenant, code__isnull=False)
        self.assertEqual(rows.count(), 15)
        refreshed = ExpenseCategory.all_objects.get(pk=fuel.pk)
        self.assertEqual(refreshed.code, 'vehicle_fuel')
        self.assertEqual(refreshed.name, 'Gorivo')
        self.assertIsNone(refreshed.default_account_id)

    def test_legacy_named_categories_keep_null_code_and_mapped_accounts(self):
        from accounting.services.chart import provision_tenant_chart
        from accounting.services.rrif_import import import_rrif_chart

        import_rrif_chart(clear=True)
        provision_tenant_chart(self.tenant)
        apply_finestar_expense_categories(self.tenant)

        legacy_names = {name for name, _account in FINE_STAR_EXPENSE_CATEGORIES}
        legacy = ExpenseCategory.all_objects.filter(tenant=self.tenant, name__in=legacy_names)
        self.assertEqual(legacy.count(), len(legacy_names))
        for row in legacy:
            self.assertIsNone(row.code)
            self.assertIsNotNone(row.default_account_id)

        vehicle = ExpenseCategory.all_objects.filter(tenant=self.tenant, code__startswith='vehicle_')
        self.assertEqual(vehicle.count(), 15)
        self.assertTrue(all(row.default_account_id is None for row in vehicle))

        overlap = set(legacy.values_list('pk', flat=True)) & set(vehicle.values_list('pk', flat=True))
        self.assertFalse(overlap)

    def test_same_vehicle_code_allowed_on_another_tenant(self):
        apply_finestar_vehicle_expense_categories(self.tenant)
        apply_finestar_vehicle_expense_categories(self.other)
        self.assertEqual(
            ExpenseCategory.all_objects.filter(code='vehicle_insurance_compulsory').count(),
            2,
        )
