"""Vehicle identity constraints, normalization, and sanctioned FixedAsset link."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounting.models import (
    AccountType,
    ChartOfAccounts,
    DepreciationMethod,
    FixedAsset,
    FixedAssetOrigin,
    FixedAssetStatus,
    JournalEntry,
    Vehicle,
)
from domains.assets.services.vehicle import link_vehicle_to_fixed_asset
from tenants.models import Tenant


class VehicleModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.tenant = Tenant.objects.create(slug='vehicle-a', name='Vehicle A')
        cls.other = Tenant.objects.create(slug='vehicle-b', name='Vehicle B')
        cls.user = User.objects.create_user(username='vehicle-user', password='test')
        cls.accounts = cls._accounts(cls.tenant)
        cls.other_accounts = cls._accounts(cls.other)
        cls.purchase = cls._je(cls.tenant, '202605-VP')
        cls.other_purchase = cls._je(cls.other, '202605-OP')
        cls.asset = cls._asset(
            cls.tenant,
            cls.accounts,
            cls.purchase,
            vin='WVWZZZCD8PW153457',
            name='VW Golf',
        )
        cls.other_asset = cls._asset(
            cls.other,
            cls.other_accounts,
            cls.other_purchase,
            vin='WVGZZZC1ZPY022544',
            name='VW T-Cross',
        )

    @staticmethod
    def _accounts(tenant):
        account_type = AccountType.all_objects.create(tenant=tenant, name='asset')
        return {
            code: ChartOfAccounts.all_objects.create(
                tenant=tenant,
                account_code=code,
                account_name=code,
                account_type=account_type,
                is_postable=True,
            )
            for code in ('0373', '032001', '0393', '4314')
        }

    @classmethod
    def _je(cls, tenant, number):
        return JournalEntry.all_objects.create(
            tenant=tenant,
            entry_number=number,
            entry_date=date(2026, 5, 1),
            description=number,
            status='posted',
            created_by=cls.user,
        )

    @staticmethod
    def _asset(tenant, accounts, purchase, *, vin, name):
        return FixedAsset.all_objects.create(
            tenant=tenant,
            name=name,
            vin=vin,
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('100.00'),
            purchase_date=date(2026, 5, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=accounts['0373'],
            asset_account=accounts['032001'],
            accumulated_depreciation_account=accounts['0393'],
            depreciation_expense_account=accounts['4314'],
            purchase_journal_entry=purchase,
        )

    def _vehicle(self, **overrides):
        values = {
            'tenant': self.tenant,
            'name': 'VW Golf',
            'vin': 'WVWZZZCD8PW153457',
        }
        values.update(overrides)
        return Vehicle.all_objects.create(**values)

    def test_vin_unique_per_tenant(self):
        self._vehicle()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._vehicle(name='Duplikat')

    def test_same_vin_allowed_in_other_tenant(self):
        self._vehicle()
        other = self._vehicle(tenant=self.other, name='Golf 2')
        self.assertEqual(other.vin, 'WVWZZZCD8PW153457')

    def test_save_normalizes_vin_and_blank_plate(self):
        vehicle = self._vehicle(vin='  wvwzzzcd8pw153457 ', registration_plate='   ')
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.vin, 'WVWZZZCD8PW153457')
        self.assertEqual(vehicle.registration_plate, '')

    def test_save_normalizes_registration_plate(self):
        vehicle = self._vehicle(vin='', registration_plate='  zg 1234   ab ')
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.vin, '')
        self.assertEqual(vehicle.registration_plate, 'ZG 1234 AB')

    def test_vin_format_constraint_holds_when_save_bypassed(self):
        vehicle = self._vehicle()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vehicle.all_objects.filter(pk=vehicle.pk).update(vin='abc')

    def test_padded_plate_constraint_holds_when_save_bypassed(self):
        vehicle = self._vehicle()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vehicle.all_objects.filter(pk=vehicle.pk).update(registration_plate='   ')

    def test_requires_vin_or_plate(self):
        self._vehicle(vin='WVWZZZCD8PW153457', registration_plate='')
        self._vehicle(name='Samo tablica', vin='', registration_plate='ZG 1234 AB')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vehicle.all_objects.create(tenant=self.tenant, name='Prazno', vin='', registration_plate='')

    def test_clean_rejects_cross_tenant_fixed_asset(self):
        vehicle = Vehicle(
            tenant=self.tenant,
            name='Golf',
            vin='WVWZZZCD8PW153457',
            fixed_asset=self.other_asset,
        )
        with self.assertRaises(ValidationError) as ctx:
            vehicle.full_clean()
        self.assertIn('fixed_asset', ctx.exception.message_dict)

    def test_clean_rejects_vin_mismatch(self):
        vehicle = Vehicle(
            tenant=self.tenant,
            name='Golf',
            vin='WAUZZZF86RN003268',
            fixed_asset=self.asset,
        )
        with self.assertRaises(ValidationError) as ctx:
            vehicle.full_clean()
        self.assertIn('fixed_asset', ctx.exception.message_dict)

    def test_link_rejects_cross_tenant(self):
        vehicle = self._vehicle()
        with self.assertRaises(ValidationError):
            link_vehicle_to_fixed_asset(vehicle, self.other_asset)

    def test_link_rejects_vin_mismatch(self):
        vehicle = self._vehicle(vin='WAUZZZF86RN003268')
        with self.assertRaises(ValidationError):
            link_vehicle_to_fixed_asset(vehicle, self.asset)

    def test_link_allows_vin_only_on_vehicle(self):
        asset = self._asset(
            self.tenant,
            self.accounts,
            self._je(self.tenant, '202605-NOVIN'),
            vin='',
            name='Bez VIN-a',
        )
        vehicle = self._vehicle(vin='WAUZZZF86RN003268', name='Audi')
        linked = link_vehicle_to_fixed_asset(vehicle, asset)
        self.assertEqual(linked.fixed_asset_id, asset.pk)

    def test_link_allows_vin_only_on_asset(self):
        vehicle = self._vehicle(vin='', registration_plate='ZG 1234 AB', name='Golf tablica')
        linked = link_vehicle_to_fixed_asset(vehicle, self.asset)
        self.assertEqual(linked.fixed_asset_id, self.asset.pk)

    def test_link_rejects_second_vehicle_on_same_asset(self):
        first = self._vehicle()
        link_vehicle_to_fixed_asset(first, self.asset)
        second = self._vehicle(vin='', registration_plate='ZG 9999 ZZ', name='Drugo')
        with self.assertRaises(ValidationError) as ctx:
            link_vehicle_to_fixed_asset(second, self.asset)
        self.assertIn('drugo vozilo', str(ctx.exception))

    def test_link_is_idempotent_for_same_pair(self):
        vehicle = self._vehicle()
        first = link_vehicle_to_fixed_asset(vehicle, self.asset)
        second = link_vehicle_to_fixed_asset(vehicle, self.asset)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Vehicle.all_objects.filter(fixed_asset=self.asset).count(), 1)


class VehicleBackfillCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.tenant = Tenant.objects.create(slug='vehicle-bf', name='Vehicle BF')
        cls.user = User.objects.create_user(username='vehicle-bf', password='test')
        account_type = AccountType.all_objects.create(tenant=cls.tenant, name='asset')
        cls.accounts = {
            code: ChartOfAccounts.all_objects.create(
                tenant=cls.tenant,
                account_code=code,
                account_name=code,
                account_type=account_type,
                is_postable=True,
            )
            for code in ('0373', '032001', '0393', '4314')
        }
        purchase = JournalEntry.all_objects.create(
            tenant=cls.tenant,
            entry_number='202605-BF',
            entry_date=date(2026, 5, 1),
            description='BF',
            status='posted',
            created_by=cls.user,
        )
        cls.asset = FixedAsset.all_objects.create(
            tenant=cls.tenant,
            name='VW Golf',
            vin='WVWZZZCD8PW153457',
            registration_plate='zg 1234 ab',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('100.00'),
            purchase_date=date(2026, 5, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=cls.accounts['0373'],
            asset_account=cls.accounts['032001'],
            accumulated_depreciation_account=cls.accounts['0393'],
            depreciation_expense_account=cls.accounts['4314'],
            purchase_journal_entry=purchase,
        )

    def test_dry_run_writes_nothing(self):
        out = StringIO()
        call_command(
            'backfill_vehicles_from_fixed_assets',
            '--tenant',
            'vehicle-bf',
            stdout=out,
        )
        self.assertIn('dry-run', out.getvalue())
        self.assertEqual(Vehicle.all_objects.count(), 0)
        self.asset.refresh_from_db()
        self.assertFalse(hasattr(self.asset, 'vehicle') and getattr(self.asset, 'vehicle', None) is not None)
        self.assertFalse(Vehicle.all_objects.filter(fixed_asset=self.asset).exists())

    def test_execute_is_idempotent(self):
        call_command(
            'backfill_vehicles_from_fixed_assets',
            '--tenant',
            'vehicle-bf',
            '--execute',
        )
        self.assertEqual(Vehicle.all_objects.count(), 1)
        vehicle = Vehicle.all_objects.get()
        self.assertEqual(vehicle.vin, 'WVWZZZCD8PW153457')
        self.assertEqual(vehicle.registration_plate, 'ZG 1234 AB')
        self.assertEqual(vehicle.fixed_asset_id, self.asset.pk)

        out = StringIO()
        call_command(
            'backfill_vehicles_from_fixed_assets',
            '--tenant',
            'vehicle-bf',
            '--execute',
            stdout=out,
        )
        self.assertEqual(Vehicle.all_objects.count(), 1)
        self.assertIn('already_linked', out.getvalue())
        self.assertIn('created=0', out.getvalue())
