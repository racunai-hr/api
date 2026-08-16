"""Testovi amortizacije — T-Cross prvi mjesec."""

from decimal import Decimal

from django.test import TestCase

from accounts.models import AuditLog
from accounting.models import DepreciationSchedule, FixedAssetStatus
from accounting.tests.test_fixed_assets_base import (
    TCROSS_ACQUISITION_COST,
    TCROSS_IN_SERVICE_DATE,
    FixedAssetSeedMixin,
)
from domains.assets.services.depreciation import (
    generate_monthly_depreciation,
    post_depreciation,
    run_monthly_depreciation_for_tenant,
)

USEFUL_LIFE_MONTHS = 60
EXPECTED_MONTHLY = Decimal('133.33')


class FixedAssetDepreciationTests(FixedAssetSeedMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.asset = self.create_tcross_asset()
        self.asset.useful_life_months = USEFUL_LIFE_MONTHS
        self.asset.residual_value = Decimal('0.00')
        self.asset.save(update_fields=['useful_life_months', 'residual_value', 'updated_at'])
        self.activate_asset(self.asset)

    def test_generate_monthly_depreciation_tcros_first_month(self):
        schedule = generate_monthly_depreciation(self.asset, 2026, 7)
        self.assertIsNotNone(schedule)
        self.assertEqual(schedule.amount, EXPECTED_MONTHLY)
        self.assertIsNone(schedule.journal_entry_id)

    def test_post_depreciation_creates_journal_d_4314_k_0393(self):
        schedule = generate_monthly_depreciation(self.asset, 2026, 7)
        entry = post_depreciation(schedule, self.user)

        lines = {(line.account.account_code, line.debit_amount, line.credit_amount)
                 for line in entry.lines.all()}
        self.assertIn(('4314', EXPECTED_MONTHLY, Decimal('0.00')), lines)
        self.assertIn(('0393', Decimal('0.00'), EXPECTED_MONTHLY), lines)

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.accumulated_depreciation, EXPECTED_MONTHLY)
        self.assertEqual(
            self.asset.current_book_value,
            TCROSS_ACQUISITION_COST - EXPECTED_MONTHLY,
        )

    def test_depreciation_idempotent_per_asset_period(self):
        first = generate_monthly_depreciation(self.asset, 2026, 7)
        second = generate_monthly_depreciation(self.asset, 2026, 7)
        self.assertEqual(first.pk, second.pk)

        entry = post_depreciation(first, self.user)
        again = post_depreciation(first, self.user)
        self.assertEqual(entry.pk, again.pk)
        self.assertEqual(
            DepreciationSchedule.all_objects.filter(fixed_asset=self.asset).count(),
            1,
        )

    def test_run_monthly_depreciation_audit_log(self):
        result = run_monthly_depreciation_for_tenant(
            self.tenant,
            self.user,
            year=2026,
            month=7,
        )
        self.assertEqual(len(result['processed']), 1)

        audit = AuditLog.all_objects.get(
            tenant=self.tenant,
            model_name='FixedAsset',
            object_id=str(self.asset.pk),
            action='depreciation_posted',
        )
        self.assertEqual(audit.changes['amount'], str(EXPECTED_MONTHLY))

    def test_skips_in_preparation_assets(self):
        golf = self.create_golf_asset()
        golf.useful_life_months = USEFUL_LIFE_MONTHS
        golf.save(update_fields=['useful_life_months', 'updated_at'])

        result = run_monthly_depreciation_for_tenant(
            self.tenant,
            self.user,
            year=2026,
            month=7,
        )
        processed_ids = {item['asset_id'] for item in result['processed']}
        self.assertIn(self.asset.pk, processed_ids)
        self.assertNotIn(golf.pk, processed_ids)

    def test_dry_run_does_not_post(self):
        result = run_monthly_depreciation_for_tenant(
            self.tenant,
            self.user,
            year=2026,
            month=7,
            dry_run=True,
        )
        self.assertTrue(result['dry_run'])
        self.assertEqual(len(result['processed']), 1)
        self.assertEqual(DepreciationSchedule.all_objects.count(), 0)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, FixedAssetStatus.ACTIVE)
        self.assertEqual(self.asset.accumulated_depreciation, Decimal('0.00'))

    def test_no_depreciation_before_in_service_month(self):
        schedule = generate_monthly_depreciation(self.asset, 2026, 6)
        self.assertIsNone(schedule)
