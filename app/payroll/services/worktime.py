"""Dnevna evidencija radnog vremena i satnice iz kataloga vrsta rada."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from payroll.models import (
    EmploymentContract,
    WorkTimeEntry,
    WorkTimeMonth,
    WorkTimeRateOverride,
    WorkTimeType,
)
from payroll.services.lifecycle import mark_worktime_month_status

STANDARD_MONTHLY_HOURS = Decimal('173.33')
TWO = Decimal('0.01')


def ensure_default_work_time_types() -> None:
    """Katalog vrsta. legal_min_pct je početna vrijednost — potvrđuje računovođa."""
    seeds = [
        ('REGULAR', 'Redovni rad', 'work', 'employer', True, Decimal('0'), None, 'regular'),
        ('OVERTIME', 'Prekovremeni rad', 'work', 'employer', True, Decimal('50'), Decimal('50'), 'overtime'),
        ('NIGHT', 'Noćni rad', 'work', 'employer', True, Decimal('10'), Decimal('10'), 'night'),
        ('SUNDAY', 'Nedjelja', 'work', 'employer', True, Decimal('50'), Decimal('35'), 'sunday'),
        ('HOLIDAY', 'Blagdan', 'work', 'employer', True, Decimal('50'), Decimal('50'), 'holiday'),
        ('ANNUAL_LEAVE', 'Godišnji odmor', 'absence', 'employer', False, Decimal('0'), None, 'leave'),
        ('SICK_EMPLOYER', 'Bolovanje poslodavac', 'absence', 'employer', False, Decimal('0'), None, 'sick'),
        ('SICK_HZZO', 'Bolovanje HZZO', 'absence', 'hzzo', False, Decimal('0'), None, 'sick_hzzo'),
    ]
    for code, name, category, payer, counts, default_pct, legal_min, bucket in seeds:
        WorkTimeType.objects.update_or_create(
            code=code,
            defaults={
                'name': name,
                'category': category,
                'payer': payer,
                'counts_as_work_hours': counts,
                'default_pct_increase': default_pct,
                'legal_min_pct': legal_min,
                'joppd_hours_bucket': bucket,
                'effective_basis': 'PAYROLL_MONTH',
                'valid_from': date(2024, 1, 1),
            },
        )


def _month(tenant, employee, year: int, month: int) -> WorkTimeMonth:
    row, _created = WorkTimeMonth.all_objects.get_or_create(
        tenant=tenant, employee=employee, year=year, month=month,
        defaults={'status': WorkTimeMonth.OPEN},
    )
    return row


def _locked(month_row: WorkTimeMonth) -> bool:
    return month_row.status == WorkTimeMonth.LOCKED


@transaction.atomic
def upsert_entry(*, tenant, employee, on: date, work_time_type: WorkTimeType, hours: Decimal, contract=None, user=None) -> WorkTimeEntry:
    month_row = _month(tenant, employee, on.year, on.month)
    if _locked(month_row):
        raise ValidationError('Zaključana evidencija se ne može mijenjati.')
    entry, _created = WorkTimeEntry.all_objects.update_or_create(
        employee=employee, date=on, work_time_type=work_time_type,
        defaults={
            'tenant': tenant,
            'contract': contract,
            'hours': hours,
            'month_lock': month_row,
            'created_by': user,
        },
    )
    return entry


def content_hash(month_row: WorkTimeMonth) -> str:
    parts = []
    entries = WorkTimeEntry.all_objects.filter(month_lock=month_row).order_by('date', 'work_time_type_id')
    for entry in entries:
        parts.append(f'{entry.date.isoformat()}|{entry.work_time_type_id}|{entry.hours}')
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()


@transaction.atomic
def lock_month(month_row: WorkTimeMonth, user) -> WorkTimeMonth:
    mark_worktime_month_status(
        month_row,
        WorkTimeMonth.LOCKED,
        locked_by=user,
        locked_at=timezone.now(),
        content_hash=content_hash(month_row),
    )
    from payroll.services.records import audit
    audit(month_row.tenant, user, 'worktime_lock', month_row, {'hash': month_row.content_hash})
    return month_row


@transaction.atomic
def reopen_month(month_row: WorkTimeMonth, user) -> WorkTimeMonth:
    from payroll.models import PayrollPeriod, Payslip

    posted = Payslip.all_objects.filter(
        tenant=month_row.tenant, employee=month_row.employee,
        period__year=month_row.year, period__month=month_row.month, status=Payslip.POSTED,
    ).exists()
    if posted:
        raise ValidationError('Knjižena lista zahtijeva novu ispravak-listu, evidencija se ne otvara.')
    calculated = Payslip.all_objects.filter(
        tenant=month_row.tenant, employee=month_row.employee,
        period__year=month_row.year, period__month=month_row.month, status=Payslip.CALCULATED,
    )
    from payroll.services.lifecycle import mark_payslip_draft
    for payslip in calculated:
        mark_payslip_draft(payslip)
    period = PayrollPeriod.all_objects.filter(
        tenant=month_row.tenant, year=month_row.year, month=month_row.month,
    ).first()
    if period is not None and period.status == PayrollPeriod.CALCULATED:
        from payroll.services.lifecycle import mark_period_status
        mark_period_status(period, PayrollPeriod.DRAFT)
    mark_worktime_month_status(month_row, WorkTimeMonth.REOPENED, content_hash='')
    from payroll.services.records import audit
    audit(month_row.tenant, user, 'worktime_reopen', month_row, {'correction': True})
    return month_row


def rate_pct_for(contract: EmploymentContract | None, work_type: WorkTimeType, on: date) -> Decimal:
    override = None
    if contract is not None:
        override = (
            WorkTimeRateOverride.all_objects.filter(contract=contract, work_time_type=work_type, valid_from__lte=on)
            .order_by('-valid_from')
            .first()
        )
    chosen = override.rate_pct if override is not None else work_type.default_pct_increase
    if work_type.legal_min_pct is not None and chosen < work_type.legal_min_pct:
        chosen = work_type.legal_min_pct
    return chosen


def gross_for_hours(contract: EmploymentContract, hours: Decimal, work_type: WorkTimeType, on: date) -> tuple[Decimal, Decimal, Decimal]:
    pct = rate_pct_for(contract, work_type, on)
    hourly = (contract.base_gross_salary / STANDARD_MONTHLY_HOURS).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    factor = Decimal('1') + (pct / Decimal('100'))
    gross = (hours * hourly * factor).quantize(TWO, rounding=ROUND_HALF_UP)
    return gross, hourly, pct
