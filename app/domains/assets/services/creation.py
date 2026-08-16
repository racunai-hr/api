"""Kreiranje osnovnog sredstva iz temeljnice nabave."""

from __future__ import annotations

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import ChartOfAccounts, FixedAsset, FixedAssetOrigin, FixedAssetStatus, JournalEntry

_VIN_PATTERN = re.compile(r'[A-HJ-NPR-Z0-9]{17}', re.IGNORECASE)


def extract_vin_from_journal_entry(journal_entry: JournalEntry) -> str:
    for text in (journal_entry.reference, journal_entry.description):
        if not text:
            continue
        match = _VIN_PATTERN.search(text.replace(' ', ''))
        if match:
            return match.group(0).upper()
    return ''


def _construction_debit_amount(journal_entry: JournalEntry, construction_account: ChartOfAccounts) -> Decimal:
    total = Decimal('0')
    for line in journal_entry.lines.filter(account=construction_account):
        total += line.debit_amount
    if total <= Decimal('0'):
        raise ValidationError(
            f'Temeljnica nema duguje na kontu u pripremi ({construction_account.account_code}).',
        )
    return total


def _validate_accounts_tenant(tenant, *accounts: ChartOfAccounts) -> None:
    for account in accounts:
        if account.tenant_id != tenant.pk:
            raise ValidationError(f'Konto {account.account_code} ne pripada tenantu.')


@transaction.atomic
def create_fixed_asset_from_purchase(
    *,
    purchase_journal_entry: JournalEntry,
    construction_account: ChartOfAccounts,
    asset_account: ChartOfAccounts,
    accumulated_depreciation_account: ChartOfAccounts,
    depreciation_expense_account: ChartOfAccounts,
    name: str = '',
    vin: str = '',
    inventory_number: str = '',
    registration_plate: str = '',
) -> FixedAsset:
    tenant = purchase_journal_entry.tenant

    existing = FixedAsset.all_objects.filter(
        purchase_journal_entry=purchase_journal_entry,
    ).first()
    if existing:
        return existing

    if purchase_journal_entry.status != 'posted':
        raise ValidationError('Temeljnica nabave mora biti knjižena.')

    _validate_accounts_tenant(
        tenant,
        construction_account,
        asset_account,
        accumulated_depreciation_account,
        depreciation_expense_account,
    )

    acquisition_cost = _construction_debit_amount(purchase_journal_entry, construction_account)
    resolved_vin = (vin or extract_vin_from_journal_entry(purchase_journal_entry)).upper()

    if resolved_vin and FixedAsset.all_objects.filter(tenant=tenant, vin=resolved_vin).exists():
        raise ValidationError(f'VIN {resolved_vin} već postoji u kartici osnovnog sredstva.')

    defaults = {
        'tenant': tenant,
        'name': name,
        'inventory_number': inventory_number,
        'vin': resolved_vin,
        'registration_plate': registration_plate,
        'status': FixedAssetStatus.IN_PREPARATION,
        'origin': FixedAssetOrigin.PURCHASE,
        'acquisition_cost': acquisition_cost,
        'purchase_date': purchase_journal_entry.entry_date,
        'construction_account': construction_account,
        'asset_account': asset_account,
        'accumulated_depreciation_account': accumulated_depreciation_account,
        'depreciation_expense_account': depreciation_expense_account,
    }
    asset = FixedAsset(purchase_journal_entry=purchase_journal_entry, **defaults)
    asset.full_clean()
    asset.save()
    return asset
