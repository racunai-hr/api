"""HR domain facade — public, read-only API for other domains (DTO-only).

Dependency rule (``docs/architecture/DOMAIN_DEPENDENCY_MAP.md``): other domains
(e.g. Tax, for JOPPD) must consume HR/payroll data only through this facade's
frozen DTOs. They must never import ``payroll.models`` directly.

Current consumer: ``domains/tax/forms/joppd`` (Obrazac JOPPD aggregation).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

MATURITY = 'L1'
LAYER_MATURITY = {
    'payroll_read': 'L1',
}


@dataclass(frozen=True)
class PayrollEmployeeRowDTO:
    """One employee's payslip for a payroll period, flattened for Tax/JOPPD use."""

    employee_oib: str
    first_name: str
    last_name: str
    date_of_birth: date | None
    municipality_code: str
    city: str
    street: str
    work_location: str
    recipient_code: str
    income_type_code: str
    mio_pillar_both: bool

    date_from: date
    date_to: date
    is_first_month: bool
    is_last_month: bool
    hours_total: Decimal

    gross_salary: Decimal
    mio1_base: Decimal
    mio1_amount: Decimal
    mio2_amount: Decimal
    income_base: Decimal
    personal_allowance_amount: Decimal
    tax_base: Decimal
    income_tax_rate_used: Decimal
    income_tax_amount: Decimal
    net_salary: Decimal
    zo_amount: Decimal
    total_labor_cost: Decimal


@dataclass(frozen=True)
class PayrollPeriodSummaryDTO:
    year: int
    month: int
    payment_date: date
    status: str
    employee_count: int
    gross_total: Decimal
    net_total: Decimal
    income_tax_total: Decimal
    zo_total: Decimal


def get_payroll_period_rows(tenant, year: int, month: int) -> list[PayrollEmployeeRowDTO]:
    """Flattened per-employee payslip rows for a payroll period, as DTOs.

    Returns an empty list when no ``PayrollPeriod`` exists for ``tenant``/``year``/``month``.
    """
    from payroll.models import PayrollPeriod

    period = PayrollPeriod.all_objects.filter(tenant=tenant, year=year, month=month).first()
    if period is None:
        return []

    rows: list[PayrollEmployeeRowDTO] = []
    payslips = (
        period.payslips.select_related('employee', 'contract')
        .prefetch_related('earning_lines')
        .order_by('employee__last_name', 'employee__first_name')
    )
    for payslip in payslips:
        employee = payslip.employee
        contract = payslip.contract
        hours_total = sum(
            (line.hours for line in payslip.earning_lines.all()),
            Decimal('0.00'),
        )
        rows.append(
            PayrollEmployeeRowDTO(
                employee_oib=employee.oib,
                first_name=employee.first_name,
                last_name=employee.last_name,
                date_of_birth=employee.date_of_birth,
                municipality_code=employee.municipality_code,
                city=employee.city,
                street=employee.street,
                work_location=employee.work_location,
                recipient_code=(contract.recipient_code if contract else '') or '0001',
                income_type_code=(contract.income_type_code if contract else '') or '0001',
                mio_pillar_both=(employee.mio_pillar == employee.MIO_BOTH_PILLARS),
                date_from=payslip.date_from,
                date_to=payslip.date_to,
                is_first_month=payslip.is_first_month,
                is_last_month=payslip.is_last_month,
                hours_total=hours_total,
                gross_salary=payslip.gross_salary,
                mio1_base=payslip.mio1_base,
                mio1_amount=payslip.mio1_amount,
                mio2_amount=payslip.mio2_amount,
                income_base=payslip.income_base,
                personal_allowance_amount=payslip.personal_allowance_amount,
                tax_base=payslip.tax_base,
                income_tax_rate_used=payslip.income_tax_rate_used,
                income_tax_amount=payslip.income_tax_amount,
                net_salary=payslip.net_salary,
                zo_amount=payslip.zo_amount,
                total_labor_cost=payslip.total_labor_cost,
            )
        )
    return rows


def get_payroll_period_summary(tenant, year: int, month: int) -> PayrollPeriodSummaryDTO | None:
    """Summary totals for a payroll period, or ``None`` when it does not exist."""
    from payroll.models import PayrollPeriod

    period = PayrollPeriod.all_objects.filter(tenant=tenant, year=year, month=month).first()
    if period is None:
        return None

    rows = get_payroll_period_rows(tenant, year, month)
    return PayrollPeriodSummaryDTO(
        year=period.year,
        month=period.month,
        payment_date=period.payment_date,
        status=period.status,
        employee_count=len(rows),
        gross_total=sum((r.gross_salary for r in rows), Decimal('0.00')),
        net_total=sum((r.net_salary for r in rows), Decimal('0.00')),
        income_tax_total=sum((r.income_tax_amount for r in rows), Decimal('0.00')),
        zo_total=sum((r.zo_amount for r in rows), Decimal('0.00')),
    )


__all__ = [
    'MATURITY',
    'LAYER_MATURITY',
    'PayrollEmployeeRowDTO',
    'PayrollPeriodSummaryDTO',
    'get_payroll_period_rows',
    'get_payroll_period_summary',
]
