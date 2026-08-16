"""Aktivacija osnovnog sredstva — prijenos s konta u pripremi na aktivirano."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import FixedAsset, FixedAssetStatus, JournalEntry
from accounting.services.manual_journal import JournalLineInput, create_manual_journal_entry


def can_activate(asset: FixedAsset) -> tuple[bool, list[str]]:
    """Read-only provjera spremnosti za aktivaciju."""
    issues: list[str] = []

    if asset.status != FixedAssetStatus.IN_PREPARATION:
        issues.append('Status mora biti "u pripremi".')
    if asset.activation_journal_entry_id:
        issues.append('Imovina je već aktivirana.')
    if not asset.purchase_journal_entry_id:
        issues.append('Nedostaje temeljnica nabave.')
    elif asset.purchase_journal_entry.status != 'posted':
        issues.append('Temeljnica nabave mora biti knjižena.')
    if not asset.construction_account_id:
        issues.append('Nedostaje konto u pripremi.')
    if not asset.asset_account_id:
        issues.append('Nedostaje konto osnovnog sredstva.')
    if not asset.in_service_date:
        issues.append('Nedostaje datum stavljanja u uporabu.')

    return len(issues) == 0, issues


@transaction.atomic
def activate_fixed_asset(
    asset: FixedAsset,
    user: User,
    *,
    in_service_date: date | None = None,
) -> JournalEntry:
    """Aktivira OS atomskom transakcijom: JE D asset / K construction + status active."""
    ready, issues = can_activate(asset)
    if not ready:
        raise ValidationError(issues)

    service_date = in_service_date or asset.in_service_date
    if not service_date:
        raise ValidationError('Datum stavljanja u uporabu je obavezan.')

    amount = asset.acquisition_cost
    vin_label = f' VIN: {asset.vin}' if asset.vin else ''
    description = f'Aktivacija {asset.name}{vin_label}'

    entry = create_manual_journal_entry(
        asset.tenant,
        user,
        entry_date=service_date,
        description=description,
        reference=asset.vin or '',
        lines=[
            JournalLineInput(
                account_code=asset.asset_account.account_code,
                debit=amount,
                credit=Decimal('0'),
            ),
            JournalLineInput(
                account_code=asset.construction_account.account_code,
                debit=Decimal('0'),
                credit=amount,
            ),
        ],
        post=True,
    )

    asset.activation_journal_entry = entry
    asset.status = FixedAssetStatus.ACTIVE
    asset.in_service_date = service_date
    asset.save(
        update_fields=[
            'activation_journal_entry',
            'status',
            'in_service_date',
            'updated_at',
        ],
    )
    return entry
