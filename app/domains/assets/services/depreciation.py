"""Amortizacija osnovnih sredstava — izračun, knjiženje i periodični run."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import AuditLog
from accounting.models import (
    DepreciationMethod,
    DepreciationSchedule,
    FixedAsset,
    FixedAssetStatus,
    JournalEntry,
)
from accounting.services.manual_journal import JournalLineInput, create_manual_journal_entry
from tenants.models import Tenant


def _period_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _months_in_service(asset: FixedAsset, year: int, month: int) -> int:
    """Broj mjeseci amortizacije do uključivo zadanog razdoblja."""
    if not asset.in_service_date:
        return 0
    period_start = date(year, month, 1)
    if asset.in_service_date > _period_end(year, month):
        return 0
    start_year, start_month = asset.in_service_date.year, asset.in_service_date.month
    return (year - start_year) * 12 + (month - start_month) + 1


def _linear_monthly_amount(asset: FixedAsset, months_in_service: int) -> Decimal:
    if not asset.useful_life_months:
        raise ValidationError('Trajnost (mjeseci) nije postavljena.')
    if asset.depreciation_method != DepreciationMethod.LINEAR:
        raise ValidationError(f'Nepodržana metoda amortizacije: {asset.depreciation_method}.')

    depreciable = asset.acquisition_cost - asset.residual_value
    if depreciable <= Decimal('0'):
        return Decimal('0.00')

    monthly = (depreciable / asset.useful_life_months).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )
    if months_in_service >= asset.useful_life_months:
        return Decimal('0.00')
    if months_in_service == asset.useful_life_months:
        already = monthly * (asset.useful_life_months - 1)
        return depreciable - already
    return monthly


def _asset_eligible_for_period(asset: FixedAsset, year: int, month: int) -> bool:
    if asset.status != FixedAssetStatus.ACTIVE:
        return False
    if not asset.useful_life_months:
        return False
    if not asset.in_service_date:
        return False
    if asset.in_service_date > _period_end(year, month):
        return False
    months = _months_in_service(asset, year, month)
    return 0 < months <= asset.useful_life_months


def generate_monthly_depreciation(
    asset: FixedAsset,
    year: int,
    month: int,
) -> DepreciationSchedule | None:
    """Izračunava iznos za razdoblje; idempotentno vraća postojeći zapis."""
    if month < 1 or month > 12:
        raise ValidationError('Mjesec mora biti između 1 i 12.')

    existing = DepreciationSchedule.all_objects.filter(
        fixed_asset=asset,
        year=year,
        month=month,
    ).first()
    if existing:
        return existing

    if not _asset_eligible_for_period(asset, year, month):
        return None

    months_in_service = _months_in_service(asset, year, month)
    amount = _linear_monthly_amount(asset, months_in_service)
    if amount <= Decimal('0'):
        return None

    schedule = DepreciationSchedule(
        tenant=asset.tenant,
        fixed_asset=asset,
        year=year,
        month=month,
        amount=amount,
    )
    schedule.full_clean()
    schedule.save()
    return schedule


@transaction.atomic
def post_depreciation(
    schedule: DepreciationSchedule,
    user: User,
) -> JournalEntry:
    """Knjiži amortizaciju za razdoblje; idempotentno ako je već knjiženo."""
    if schedule.journal_entry_id:
        entry = schedule.journal_entry
        if entry.status == 'posted':
            return entry
        raise ValidationError('Plan amortizacije ima temeljnicu koja nije knjižena.')

    asset = schedule.fixed_asset
    if asset.status != FixedAssetStatus.ACTIVE:
        raise ValidationError('Amortizacija je moguća samo za aktivnu imovinu.')

    entry_date = _period_end(schedule.year, schedule.month)
    vin_label = f' VIN: {asset.vin}' if asset.vin else ''
    description = (
        f'Amortizacija {asset.name}{vin_label} — {schedule.month:02d}/{schedule.year}'
    )

    entry = create_manual_journal_entry(
        asset.tenant,
        user,
        entry_date=entry_date,
        description=description,
        reference=asset.vin or '',
        lines=[
            JournalLineInput(
                account_code=asset.depreciation_expense_account.account_code,
                debit=schedule.amount,
                credit=Decimal('0'),
            ),
            JournalLineInput(
                account_code=asset.accumulated_depreciation_account.account_code,
                debit=Decimal('0'),
                credit=schedule.amount,
            ),
        ],
        post=True,
    )

    schedule.journal_entry = entry
    schedule.save(update_fields=['journal_entry'])

    AuditLog.all_objects.create(
        tenant=asset.tenant,
        user=user,
        action='depreciation_posted',
        model_name='FixedAsset',
        object_id=str(asset.pk),
        changes={
            'year': schedule.year,
            'month': schedule.month,
            'amount': str(schedule.amount),
            'journal_entry_id': entry.pk,
            'schedule_id': schedule.pk,
        },
    )
    return entry


def run_monthly_depreciation_for_tenant(
    tenant: Tenant,
    user: User,
    *,
    year: int,
    month: int,
    dry_run: bool = False,
) -> dict:
    """Generira i knjiži amortizaciju za sve aktivne OS tenant-a u razdoblju."""
    results: dict = {
        'tenant': tenant.slug,
        'year': year,
        'month': month,
        'dry_run': dry_run,
        'processed': [],
        'skipped': [],
    }

    assets = FixedAsset.all_objects.filter(
        tenant=tenant,
        status=FixedAssetStatus.ACTIVE,
    ).order_by('pk')

    for asset in assets:
        if not _asset_eligible_for_period(asset, year, month):
            results['skipped'].append({'asset_id': asset.pk, 'reason': 'not_eligible'})
            continue

        if dry_run:
            months_in_service = _months_in_service(asset, year, month)
            amount = _linear_monthly_amount(asset, months_in_service)
            results['processed'].append({
                'asset_id': asset.pk,
                'name': asset.name,
                'amount': str(amount),
                'dry_run': True,
            })
            continue

        with transaction.atomic():
            schedule = generate_monthly_depreciation(asset, year, month)
            if schedule is None:
                results['skipped'].append({'asset_id': asset.pk, 'reason': 'no_amount'})
                continue
            entry = post_depreciation(schedule, user)
            results['processed'].append({
                'asset_id': asset.pk,
                'name': asset.name,
                'amount': str(schedule.amount),
                'journal_entry_id': entry.pk,
                'schedule_id': schedule.pk,
            })

    return results
