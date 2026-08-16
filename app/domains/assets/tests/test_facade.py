"""Facade re-export testovi za Assets domenu."""

from django.test import SimpleTestCase

import domains.assets as assets_package
import domains.assets.facade as facade
from accounting.services import fixed_assets as legacy_fixed_assets


class AssetsFacadeTests(SimpleTestCase):
    def test_facade_exports_public_api(self):
        expected = {
            'activate_fixed_asset',
            'can_activate',
            'create_fixed_asset_from_purchase',
            'extract_vin_from_journal_entry',
            'generate_monthly_depreciation',
            'post_depreciation',
            'run_monthly_depreciation_for_tenant',
        }
        self.assertTrue(expected.issubset(set(facade.__all__)))
        for name in expected:
            self.assertTrue(hasattr(facade, name))
            self.assertTrue(callable(getattr(facade, name)))

    def test_legacy_reexport_matches_facade(self):
        for name in legacy_fixed_assets.__all__:
            self.assertIs(getattr(legacy_fixed_assets, name), getattr(facade, name))

    def test_package_maturity_l3(self):
        self.assertEqual(assets_package.MATURITY, 'L3')
        self.assertEqual(facade.MATURITY, 'L3')
