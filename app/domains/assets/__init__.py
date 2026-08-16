"""Assets domain — fixed assets, depreciation.

Maturity: L3
Legacy apps: accounting (FixedAsset)
"""

from domains.assets.facade import MATURITY
from domains.assets.services import (
    activate_fixed_asset,
    can_activate,
    create_fixed_asset_from_purchase,
    extract_vin_from_journal_entry,
    generate_monthly_depreciation,
    post_depreciation,
    run_monthly_depreciation_for_tenant,
)

__all__ = [
    'MATURITY',
    'activate_fixed_asset',
    'can_activate',
    'create_fixed_asset_from_purchase',
    'extract_vin_from_journal_entry',
    'generate_monthly_depreciation',
    'post_depreciation',
    'run_monthly_depreciation_for_tenant',
]
