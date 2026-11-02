"""Čuvari statusa obračuna. Prijelazi koji mijenjaju knjiženo stanje ostaju ovdje."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from payroll.models import PayrollPeriod, Payslip


def assert_payslip_mutable(payslip: Payslip) -> None:
    if payslip.status == Payslip.POSTED or payslip.period.status == PayrollPeriod.POSTED:
        raise ValidationError('Knjižena obračunska lista ne može se ponovno obračunati.')


def assert_period_open_for_joppd(status: str) -> None:
    if status not in (PayrollPeriod.CALCULATED, PayrollPeriod.POSTED, PayrollPeriod.APPROVED, PayrollPeriod.CLOSED):
        raise ValidationError(
            'JOPPD se može sastaviti samo za obračunat ili knjižen period plaće.'
        )


def mark_payslip_calculated(payslip: Payslip) -> None:
    payslip.status = Payslip.CALCULATED


def mark_payslip_draft(payslip: Payslip) -> None:
    payslip.status = Payslip.DRAFT
    payslip.save(update_fields=['status'])


def mark_period_calculated(period: PayrollPeriod) -> None:
    period.status = PayrollPeriod.CALCULATED
    period.save(update_fields=['status'])


def mark_period_posted(period: PayrollPeriod) -> None:
    period.status = PayrollPeriod.POSTED


def mark_payslips_posted(period: PayrollPeriod) -> None:
    Payslip.all_objects.filter(period=period).update(status=Payslip.POSTED)


def mark_period_status(period: PayrollPeriod, status: str) -> None:
    period.status = status
    period.save(update_fields=['status', 'updated_at'])


def mark_worktime_month_status(month, status: str, **extra) -> None:
    month.status = status
    for key, value in extra.items():
        setattr(month, key, value)
    month.save()
