"""Razrješavanje zakonskih parametara prema effective_basis svakog pravila."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Q

from payroll.models import Employee, MunicipalityIncomeTaxRate, PayrollLegalParameter, PayrollPeriod, PayrollTaxYear, Payslip

MIO_REDUCTION_FLOOR = Decimal('700.00')
MIO_REDUCTION_FLAT = Decimal('300.00')

def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def basis_date(basis: str, period: PayrollPeriod, payslip: Payslip, event_date: date | None = None) -> date:
    if basis == PayrollLegalParameter.PAYMENT_DATE:
        return _as_date(period.payment_date)
    if basis == PayrollLegalParameter.PAYROLL_MONTH:
        return date(period.year, period.month, 1)
    if basis == PayrollLegalParameter.ENTITLEMENT_PERIOD:
        return _as_date(payslip.date_from)
    if event_date is not None:
        return _as_date(event_date)
    return _as_date(payslip.date_to)


def lookup_parameter(code: str, on: date) -> PayrollLegalParameter | None:
    return (
        PayrollLegalParameter.objects.filter(code=code, valid_from__lte=on)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=on))
        .order_by('-valid_from')
        .first()
    )


def municipality_rate_on(employee: Employee, on: date) -> MunicipalityIncomeTaxRate:
    code = (employee.municipality_code or '').strip()
    if not code:
        from django.core.exceptions import ValidationError
        raise ValidationError(f'Zaposlenik {employee} nema šifru JLS prebivališta.')
    dated = (
        MunicipalityIncomeTaxRate.objects.filter(code=code, valid_from__lte=on)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=on))
        .order_by('-valid_from')
        .first()
    )
    if dated is not None:
        return dated
    from payroll.services.calculate import get_municipality_rate
    return get_municipality_rate(employee, on.year)


@dataclass(frozen=True)
class ResolvedRules:
    mio1_rate: Decimal
    mio2_rate: Decimal
    mio1_only_rate: Decimal
    zo_rate: Decimal
    personal_allowance_base: Decimal
    tax_bracket_annual: Decimal
    minimum_wage_gross: Decimal
    mio_ceiling: Decimal
    mio_floor: Decimal
    mio_flat: Decimal
    protected_reference_net: Decimal
    municipality: MunicipalityIncomeTaxRate
    snapshot: dict


def _pick(code: str, basis: str, fallback: Decimal, period, payslip, snapshot: dict) -> Decimal:
    on = basis_date(basis, period, payslip)
    row = lookup_parameter(code, on)
    if row is not None:
        snapshot[code] = {
            'value': str(row.value),
            'on': on.isoformat(),
            'basis': row.effective_basis,
            'id': row.id,
        }
        return Decimal(row.value)
    snapshot[code] = {
        'value': str(fallback),
        'on': on.isoformat(),
        'basis': basis,
        'id': None,
        'source': 'payroll_tax_year',
    }
    return Decimal(fallback)


def resolve_rules(payslip: Payslip, event_date: date | None = None) -> ResolvedRules:
    from payroll.services.calculate import get_tax_year

    period = payslip.period
    tax_year: PayrollTaxYear = get_tax_year(period.year)
    snapshot: dict = {}
    payment_on = basis_date(PayrollLegalParameter.PAYMENT_DATE, period, payslip, event_date)
    municipality = municipality_rate_on(payslip.employee, payment_on)
    snapshot['MUNICIPALITY_RATE'] = {
        'id': municipality.id,
        'on': payment_on.isoformat(),
        'basis': municipality.effective_basis or PayrollLegalParameter.PAYMENT_DATE,
        'lower_rate': str(municipality.lower_rate),
        'higher_rate': str(municipality.higher_rate),
    }
    return ResolvedRules(
        mio1_rate=_pick('MIO1_RATE', PayrollLegalParameter.PAYMENT_DATE, tax_year.mio1_rate, period, payslip, snapshot),
        mio2_rate=_pick('MIO2_RATE', PayrollLegalParameter.PAYMENT_DATE, tax_year.mio2_rate, period, payslip, snapshot),
        mio1_only_rate=_pick('MIO1_ONLY_RATE', PayrollLegalParameter.PAYMENT_DATE, tax_year.mio1_only_rate, period, payslip, snapshot),
        zo_rate=_pick('ZO_RATE', PayrollLegalParameter.PAYMENT_DATE, tax_year.zo_rate, period, payslip, snapshot),
        personal_allowance_base=_pick(
            'PERSONAL_ALLOWANCE_BASE', PayrollLegalParameter.PAYMENT_DATE,
            tax_year.basic_personal_allowance, period, payslip, snapshot,
        ),
        tax_bracket_annual=_pick(
            'TAX_BRACKET_ANNUAL', PayrollLegalParameter.PAYMENT_DATE,
            tax_year.income_tax_annual_bracket_threshold, period, payslip, snapshot,
        ),
        minimum_wage_gross=_pick(
            'MIN_WAGE_GROSS_FULLTIME', PayrollLegalParameter.PAYROLL_MONTH,
            tax_year.minimum_wage_gross, period, payslip, snapshot,
        ),
        mio_ceiling=_pick(
            'MIO1_REDUCTION_CEILING', PayrollLegalParameter.PAYROLL_MONTH,
            tax_year.mio1_base_reduction_threshold, period, payslip, snapshot,
        ),
        mio_floor=_pick(
            'MIO1_REDUCTION_FLOOR', PayrollLegalParameter.PAYROLL_MONTH,
            MIO_REDUCTION_FLOOR, period, payslip, snapshot,
        ),
        mio_flat=_pick(
            'MIO1_REDUCTION_FLAT', PayrollLegalParameter.PAYROLL_MONTH,
            MIO_REDUCTION_FLAT, period, payslip, snapshot,
        ),
        protected_reference_net=_pick(
            'PROTECTED_REFERENCE_NET', PayrollLegalParameter.PAYMENT_DATE,
            tax_year.protected_account_reference_net, period, payslip, snapshot,
        ),
        municipality=municipality,
        snapshot=snapshot,
    )
