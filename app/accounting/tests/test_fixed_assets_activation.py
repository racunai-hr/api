"""Testovi aktivacije osnovnih sredstava — T-Cross (#49) i Golf (#3)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import FixedAssetStatus
from accounting.tests.test_fixed_assets_base import (
    GOLF_ACQUISITION_COST,
    GOLF_IN_SERVICE_DATE,
    TCROSS_ACQUISITION_COST,
    TCROSS_IN_SERVICE_DATE,
    FixedAssetSeedMixin,
)
from domains.assets.services.activation import activate_fixed_asset, can_activate


class FixedAssetActivationTests(FixedAssetSeedMixin, TestCase):
    def test_can_activate_tcros_ready(self):
        asset = self.create_tcross_asset()
        ready, issues = can_activate(asset)
        self.assertTrue(ready)
        self.assertEqual(issues, [])

    def test_can_activate_missing_in_service_date(self):
        asset = self.create_tcross_asset(in_service_date=None)
        ready, issues = can_activate(asset)
        self.assertFalse(ready)
        self.assertIn('Nedostaje datum stavljanja u uporabu.', issues)

    def test_activate_tcros_journal_entry_0373_to_032001(self):
        """Replika faze 5a — D 032001 / K 0373 na T-Cross seed."""
        asset = self.create_tcross_asset()
        entry = self.activate_asset(asset)

        asset.refresh_from_db()
        self.assertEqual(asset.status, FixedAssetStatus.ACTIVE)
        self.assertEqual(asset.in_service_date, TCROSS_IN_SERVICE_DATE)
        self.assertEqual(asset.activation_journal_entry_id, entry.pk)
        self.assertEqual(entry.status, 'posted')

        lines = {(line.account.account_code, line.debit_amount, line.credit_amount)
                 for line in entry.lines.all()}
        self.assertIn(('032001', TCROSS_ACQUISITION_COST, Decimal('0.00')), lines)
        self.assertIn(('0373', Decimal('0.00'), TCROSS_ACQUISITION_COST), lines)

    def test_activate_golf_after_tcross(self):
        """Fine Star Golf (#3) — aktivacija nakon T-Cross."""
        tcross = self.create_tcross_asset()
        golf = self.create_golf_asset()

        self.activate_asset(tcross)
        entry = self.activate_asset(golf, in_service_date=GOLF_IN_SERVICE_DATE)

        golf.refresh_from_db()
        self.assertEqual(golf.status, FixedAssetStatus.ACTIVE)
        self.assertEqual(golf.activation_journal_entry_id, entry.pk)

        credit_line = entry.lines.get(account__account_code='0373')
        self.assertEqual(credit_line.credit_amount, GOLF_ACQUISITION_COST)

    def test_activate_rejects_already_active(self):
        asset = self.create_tcross_asset()
        self.activate_asset(asset)
        with self.assertRaises(ValidationError):
            activate_fixed_asset(asset, self.user)

    def test_activate_atomic_rollback_on_post_failure(self):
        asset = self.create_tcross_asset()
        asset.in_service_date = TCROSS_IN_SERVICE_DATE
        asset.save(update_fields=['in_service_date', 'updated_at'])

        from unittest.mock import patch

        with patch(
            'domains.assets.services.activation.create_manual_journal_entry',
            side_effect=ValidationError('Simulirana greška knjiženja.'),
        ):
            with self.assertRaises(ValidationError):
                activate_fixed_asset(asset, self.user)

        asset.refresh_from_db()
        self.assertEqual(asset.status, FixedAssetStatus.IN_PREPARATION)
        self.assertIsNone(asset.activation_journal_entry_id)
