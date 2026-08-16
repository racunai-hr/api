"""Osnovna sredstva — backward-compatible re-export (faza 6.1–6.4).

Prefer: domains.assets.facade
"""

from domains.assets.services.activation import activate_fixed_asset, can_activate
from domains.assets.services.creation import create_fixed_asset_from_purchase, extract_vin_from_journal_entry
from domains.assets.services.depreciation import (
    generate_monthly_depreciation,
    post_depreciation,
    run_monthly_depreciation_for_tenant,
)

__all__ = [
    'activate_fixed_asset',
    'can_activate',
    'create_fixed_asset_from_purchase',
    'extract_vin_from_journal_entry',
    'generate_monthly_depreciation',
    'post_depreciation',
    'run_monthly_depreciation_for_tenant',
]
