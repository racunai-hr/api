"""Assets domain services."""

from domains.assets.services.activation import activate_fixed_asset, can_activate
from domains.assets.services.creation import create_fixed_asset_from_purchase, extract_vin_from_journal_entry
from domains.assets.services.depreciation import (
    generate_monthly_depreciation,
    post_depreciation,
    run_monthly_depreciation_for_tenant,
)
from domains.assets.services.retarget import retarget_purchase_journal_entry
from domains.assets.services.vehicle import link_vehicle_to_fixed_asset

__all__ = [
    'activate_fixed_asset',
    'can_activate',
    'create_fixed_asset_from_purchase',
    'extract_vin_from_journal_entry',
    'generate_monthly_depreciation',
    'link_vehicle_to_fixed_asset',
    'post_depreciation',
    'retarget_purchase_journal_entry',
    'run_monthly_depreciation_for_tenant',
]
