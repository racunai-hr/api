"""Generiranje nacrta obračunskih lista za period na temelju aktivnih ugovora."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q

from payroll.models import EmploymentContract, PayrollPeriod, Payslip, PayslipEarningLine

TWO_PLACES = Decimal('0.01')


def _period_bounds(period: PayrollPeriod) -> tuple[date, date]:
    last_day = calendar.monthrange(period.year, period.month)[1]
    return date(period.year, period.month, 1), date(period.year, period.month, last_day)


def _prorated_base_gross(contract: EmploymentContract, date_from, date_to, period_start, period_end) -> Decimal:
    """Ugovoreni bruto × udio dana u mjesecu × postotak radnog vremena.

    Umanjenje MIO osnovice se ovdje ne dira — ono se računa na puni mjesečni bruto.
    """
    days_in_month = (period_end - period_start).days + 1
    worked_days = (date_to - date_from).days + 1
    if days_in_month <= 0 or worked_days <= 0:
        return Decimal('0.00')
    day_ratio = Decimal(worked_days) / Decimal(days_in_month)
    time_ratio = (contract.work_time_percentage or Decimal('100')) / Decimal('100')
    return (contract.base_gross_salary * day_ratio * time_ratio).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _active_contracts(period: PayrollPeriod) -> list[EmploymentContract]:
    period_start, period_end = _period_bounds(period)
    contracts = EmploymentContract.all_objects.filter(
        tenant=period.tenant,
        is_active=True,
        employee__is_active=True,
        start_date__lte=period_end,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=period_start)).select_related('employee')
    return list(contracts)


@transaction.atomic
def generate_payslips_for_period(period: PayrollPeriod) -> list[Payslip]:
    """Kreiraj nacrt Payslip + default stavku "REDOVNI RAD" za svaki aktivni ugovor.

    Idempotentno: ako Payslip za (period, employee) već postoji, preskače se.
    """
    period_start, period_end = _period_bounds(period)
    created: list[Payslip] = []

    for contract in _active_contracts(period):
        employee = contract.employee
        if Payslip.all_objects.filter(tenant=period.tenant, period=period, employee=employee).exists():
            continue

        date_from = max(contract.start_date, period_start)
        date_to = min(contract.end_date, period_end) if contract.end_date else period_end
        is_first_month = period_start <= contract.start_date <= period_end
        is_last_month = bool(contract.end_date) and period_start <= contract.end_date <= period_end

        payslip = Payslip.all_objects.create(
            tenant=period.tenant,
            period=period,
            employee=employee,
            contract=contract,
            date_from=date_from,
            date_to=date_to,
            is_first_month=is_first_month,
            is_last_month=is_last_month,
            status=Payslip.DRAFT,
        )
        from payroll.models import WorkTimeEntry, WorkTimeMonth
        from payroll.services.worktime import gross_for_hours

        locked = WorkTimeMonth.all_objects.filter(
            tenant=period.tenant, employee=employee, year=period.year, month=period.month,
            status=WorkTimeMonth.LOCKED,
        ).first()
        entries = []
        if locked is not None:
            entries = list(
                WorkTimeEntry.all_objects.filter(month_lock=locked).select_related('work_time_type')
            )
        if entries:
            for entry in entries:
                gross, hourly, pct = gross_for_hours(contract, entry.hours, entry.work_time_type, entry.date)
                PayslipEarningLine.all_objects.create(
                    tenant=period.tenant,
                    payslip=payslip,
                    name=entry.work_time_type.name,
                    hours=entry.hours,
                    factor=Decimal('1.0000') + (pct / Decimal('100')),
                    gross_amount=gross,
                    work_time_type=entry.work_time_type,
                    rate_pct=pct,
                    base_hourly_rate=hourly,
                    source='worktime',
                )
        else:
            PayslipEarningLine.all_objects.create(
                tenant=period.tenant,
                payslip=payslip,
                name='REDOVNI RAD',
                hours=Decimal('0.00'),
                factor=Decimal('1.0000'),
                gross_amount=_prorated_base_gross(contract, date_from, date_to, period_start, period_end),
                source='contract',
            )
        created.append(payslip)

    return created
