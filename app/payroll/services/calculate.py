"""Obračunski motor: bruto → doprinosi → porezna osnovica → porez → neto → za isplatu.

Slijed:

1. Bruto = stavke naknada + oporezivi dodaci. Neoporezivi primitci i obustave ne ulaze u bruto.
2. Umanjenje osnovice MIO I na zbroj bruto isplata radnika u istom mjesecu.
   Ne ovisi o postotku radnog vremena ni o danima osiguranja.
   <= 700: umanjenje 300; 700,01–1300: 0,5 × (1300 − mjesečni bruto); iznad 1300: 0.
   Prag 0 (npr. 2022. uzorak) isključuje umanjenje.
3. Dohodak = bruto − doprinosi iz plaće.
4. Porezna osnovica = max(dohodak − osobni odbitak, 0). Odbitak se ne oduzima od neto.
5. Neto = dohodak − porez.
6. Za isplatu = neto + neoporezivi primitci − obustave.
7. ZO = bruto × stopa. Ukupni trošak = bruto + ZO.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from payroll.models import (
    Employee,
    MunicipalityIncomeTaxRate,
    PayrollPeriod,
    PayrollTaxYear,
    Payslip,
    PayslipAdjustmentLine,
    PayslipContributionLine,
)
from payroll.services.lifecycle import assert_payslip_mutable, mark_payslip_calculated

TWO_PLACES = Decimal('0.01')
MIO_REDUCTION_FLOOR = Decimal('700.00')
MIO_REDUCTION_FLAT = Decimal('300.00')


def _round(value: Decimal) -> Decimal:
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def get_tax_year(year: int) -> PayrollTaxYear:
    try:
        return PayrollTaxYear.objects.get(year=year)
    except PayrollTaxYear.DoesNotExist as exc:
        raise ValidationError(
            f'Nema definiranih zakonskih parametara plaće (PayrollTaxYear) za '
            f'godinu {year}. Pokrenite `import_payroll_legal_data --year {year}` '
            f'ili unesite ručno u Django adminu.'
        ) from exc


def get_municipality_rate(employee: Employee, year: int) -> MunicipalityIncomeTaxRate:
    code = (employee.municipality_code or '').strip()
    if not code:
        raise ValidationError(
            f'Zaposlenik {employee} nema šifru JLS prebivališta — potrebna za '
            f'obračun poreza na dohodak.'
        )
    try:
        return MunicipalityIncomeTaxRate.objects.get(year=year, code=code)
    except MunicipalityIncomeTaxRate.DoesNotExist as exc:
        raise ValidationError(
            f'Nema stope poreza na dohodak za JLS {code} / godina {year}.'
        ) from exc


def mio_reduction_amount(
    monthly_gross: Decimal,
    ceiling: Decimal,
    floor: Decimal = MIO_REDUCTION_FLOOR,
    flat: Decimal = MIO_REDUCTION_FLAT,
) -> Decimal:
    """Umanjenje osnovice MIO I za mjesečni bruto. Prag 0 znači da umanjenja nema."""
    if not ceiling or ceiling <= 0 or monthly_gross <= 0:
        return Decimal('0.00')
    if monthly_gross <= floor:
        return flat
    if monthly_gross <= ceiling:
        return Decimal('0.5') * (ceiling - monthly_gross)
    return Decimal('0.00')


def _adjustment_buckets(payslip: Payslip) -> tuple[Decimal, Decimal, Decimal]:
    taxable = Decimal('0.00')
    non_taxable = Decimal('0.00')
    deductions = Decimal('0.00')
    for line in payslip.adjustment_lines.all():
        amount = abs(Decimal(line.amount))
        kind = line.kind
        if kind == PayslipAdjustmentLine.DEDUCTION or Decimal(line.amount) < 0:
            deductions += amount
        elif kind == PayslipAdjustmentLine.NON_TAXABLE_RECEIPT or not line.is_taxable:
            non_taxable += amount
        else:
            taxable += amount
    return _round(taxable), _round(non_taxable), _round(deductions)


def _gross_for_payslip(payslip: Payslip) -> tuple[Decimal, Decimal, Decimal]:
    earning_total = payslip.earning_lines.aggregate(total=Sum('gross_amount'))['total'] or Decimal('0.00')
    taxable, non_taxable, deductions = _adjustment_buckets(payslip)
    if earning_total:
        gross = _round(Decimal(earning_total) + taxable)
    elif taxable:
        gross = taxable
    else:
        gross = _round(payslip.contract.base_gross_salary if payslip.contract else Decimal('0.00'))
    return gross, non_taxable, deductions


def _minimum_wage_flags(payslip: Payslip, minimum: Decimal, gross: Decimal) -> list[dict]:
    minimum = minimum or Decimal('0.00')
    if minimum <= 0:
        return []
    percentage = Decimal('100')
    if payslip.contract_id and payslip.contract.work_time_percentage:
        percentage = payslip.contract.work_time_percentage
    required = _round(minimum * percentage / Decimal('100'))
    if gross < required:
        return [{
            'code': 'below_minimum_wage',
            'gross': str(gross),
            'minimum': str(required),
        }]
    return []


def _mio_amounts(
    payslip: Payslip, mio1_rate: Decimal, mio2_rate: Decimal, mio1_only_rate: Decimal,
    gross: Decimal, mio1_base: Decimal,
) -> tuple[Decimal, Decimal]:
    if payslip.employee.mio_pillar == Employee.MIO_FIRST_ONLY:
        mio1_amount = _round(mio1_base * mio1_only_rate / Decimal('100'))
        return mio1_amount, Decimal('0.00')
    mio1_amount = _round(mio1_base * mio1_rate / Decimal('100'))
    mio2_amount = _round(gross * mio2_rate / Decimal('100'))
    return mio1_amount, mio2_amount


def _calculate_income_tax(
    tax_base: Decimal, rate: MunicipalityIncomeTaxRate, annual_threshold: Decimal,
) -> tuple[Decimal, Decimal]:
    monthly_threshold = _round(annual_threshold / Decimal('12'))
    if tax_base <= 0:
        return Decimal('0.00'), Decimal('0.00')
    if tax_base <= monthly_threshold:
        tax = _round(tax_base * rate.lower_rate / Decimal('100'))
        return tax, rate.lower_rate
    lower_part = _round(monthly_threshold * rate.lower_rate / Decimal('100'))
    higher_part = _round((tax_base - monthly_threshold) * rate.higher_rate / Decimal('100'))
    tax = lower_part + higher_part
    effective_rate = _round((tax / tax_base) * Decimal('100')) if tax_base else Decimal('0.00')
    return tax, effective_rate


def _allocate_mio1_bases(
    ordered: list[tuple[Payslip, Decimal]],
    ceiling: Decimal,
    floor: Decimal = MIO_REDUCTION_FLOOR,
    flat: Decimal = MIO_REDUCTION_FLAT,
) -> dict[int, Decimal]:
    """Rasporedi mjesečnu MIO I osnovicu. Knjižene liste se ne diraju."""
    monthly_gross = _round(sum((gross for _payslip, gross in ordered), Decimal('0.00')))
    reduction = mio_reduction_amount(monthly_gross, ceiling, floor, flat)
    remaining = _round(max(monthly_gross - reduction, Decimal('0.00')))
    posted_used = _round(sum(
        (payslip.mio1_base for payslip, _gross in ordered if payslip.status == Payslip.POSTED),
        Decimal('0.00'),
    ))
    remaining = _round(remaining - posted_used)
    mutable = [(payslip, gross) for payslip, gross in ordered if payslip.status != Payslip.POSTED]
    allocated: dict[int, Decimal] = {}
    for index, (payslip, gross) in enumerate(mutable):
        is_last = index == len(mutable) - 1
        if is_last:
            base = max(remaining, Decimal('0.00'))
        else:
            base = min(max(remaining, Decimal('0.00')), gross)
        base = _round(min(base, gross) if gross >= 0 else Decimal('0.00'))
        if is_last:
            base = _round(max(min(remaining, gross), Decimal('0.00')))
        allocated[payslip.pk] = base
        remaining = _round(remaining - base)
    return allocated


def _apply(
    payslip: Payslip,
    rules,
    gross: Decimal,
    non_taxable: Decimal,
    deductions: Decimal,
    mio1_base: Decimal,
) -> Payslip:
    payslip.gross_salary = gross
    payslip.mio1_base = _round(mio1_base)
    mio1_amount, mio2_amount = _mio_amounts(
        payslip, rules.mio1_rate, rules.mio2_rate, rules.mio1_only_rate, gross, payslip.mio1_base,
    )
    payslip.mio1_amount = mio1_amount
    payslip.mio2_amount = mio2_amount
    contributions = _round(mio1_amount + mio2_amount)
    payslip.employee_contributions_total = contributions

    income_base = _round(gross - contributions)
    payslip.income_base = income_base
    personal_allowance = _round(
        rules.personal_allowance_base * (Decimal('1') + payslip.employee.personal_allowance_coefficient)
    )
    personal_allowance_used = min(personal_allowance, max(income_base, Decimal('0.00')))
    payslip.personal_allowance_amount = personal_allowance_used
    tax_base = max(_round(income_base - personal_allowance_used), Decimal('0.00'))
    payslip.tax_base = tax_base

    income_tax_amount, rate_used = _calculate_income_tax(tax_base, rules.municipality, rules.tax_bracket_annual)
    payslip.income_tax_amount = income_tax_amount
    payslip.income_tax_rate_used = rate_used
    payslip.surtax_amount = Decimal('0.00')

    net_salary = _round(income_base - income_tax_amount)
    payslip.net_salary = net_salary
    payslip.non_taxable_total = non_taxable
    payslip.deductions_total = deductions
    amount_to_pay = _round(net_salary + non_taxable - deductions)
    payslip.amount_to_pay = amount_to_pay

    zo_amount = _round(gross * rules.zo_rate / Decimal('100'))
    payslip.zo_amount = zo_amount
    payslip.total_labor_cost = _round(gross + zo_amount)

    payslip.protected_sick_leave_hzzo = Decimal('0.00')
    payslip.protected_sick_leave_42d = Decimal('0.00')
    payslip.protected_adjustments = Decimal('0.00')
    payslip.protected_salary_part = Decimal('0.00')
    payslip.protected_total = Decimal('0.00')
    payslip.regular_account_amount = amount_to_pay
    payslip.compliance_flags = _minimum_wage_flags(payslip, rules.minimum_wage_gross, gross)
    payslip.parameters_snapshot = rules.snapshot
    mark_payslip_calculated(payslip)
    payslip.save()
    _sync_contribution_lines(
        payslip, rules.mio1_rate, rules.mio2_rate, rules.mio1_only_rate, rules.zo_rate,
        payslip.mio1_base, mio1_amount, mio2_amount, zo_amount,
    )
    return payslip


@transaction.atomic
def calculate_payslip(payslip: Payslip) -> Payslip:
    """Izračunaj ovu listu i ranije neknjižene isplate istog radnika u istom mjesecu."""
    from payroll.services.legal_params import resolve_rules

    assert_payslip_mutable(payslip)
    period = payslip.period
    rules = resolve_rules(payslip)

    siblings = list(
        Payslip.all_objects.filter(
            tenant_id=payslip.tenant_id,
            employee_id=payslip.employee_id,
            period__year=period.year,
            period__month=period.month,
        ).select_related('contract', 'employee', 'period').order_by('payout_index', 'id')
    )
    if payslip.pk and all(row.pk != payslip.pk for row in siblings):
        siblings.append(payslip)
        siblings.sort(key=lambda row: (row.payout_index, row.id or 0))

    parts = {row.pk: _gross_for_payslip(row) for row in siblings}
    ordered = [(row, parts[row.pk][0]) for row in siblings]
    bases = _allocate_mio1_bases(ordered, rules.mio_ceiling, rules.mio_floor, rules.mio_flat)

    result = payslip
    for row in siblings:
        if row.status == Payslip.POSTED:
            continue
        if row.period.status == PayrollPeriod.POSTED:
            continue
        gross, non_taxable, deductions = parts[row.pk]
        saved = _apply(row, rules, gross, non_taxable, deductions, bases[row.pk])
        if saved.pk == payslip.pk:
            result = saved
    return result


def _sync_contribution_lines(
    payslip: Payslip,
    mio1_rate: Decimal,
    mio2_rate: Decimal,
    mio1_only_rate: Decimal,
    zo_rate: Decimal,
    mio1_base: Decimal,
    mio1_amount: Decimal,
    mio2_amount: Decimal,
    zo_amount: Decimal,
) -> None:
    payslip.contribution_lines.all().delete()
    lines = []
    if payslip.employee.mio_pillar == Employee.MIO_FIRST_ONLY:
        lines.append(
            PayslipContributionLine(
                tenant=payslip.tenant,
                payslip=payslip,
                name='DOP. ZA MIO (I. stup — generacijska solidarnost)',
                base_amount=mio1_base,
                rate_from_salary=mio1_only_rate,
                amount_from_salary=mio1_amount,
                rate_on_salary=Decimal('0.000'),
                amount_on_salary=Decimal('0.00'),
            )
        )
    else:
        lines.append(
            PayslipContributionLine(
                tenant=payslip.tenant,
                payslip=payslip,
                name='DOP. ZA MIO I. stup',
                base_amount=mio1_base,
                rate_from_salary=mio1_rate,
                amount_from_salary=mio1_amount,
                rate_on_salary=Decimal('0.000'),
                amount_on_salary=Decimal('0.00'),
            )
        )
        lines.append(
            PayslipContributionLine(
                tenant=payslip.tenant,
                payslip=payslip,
                name='DOP. ZA MIO II. stup',
                base_amount=payslip.gross_salary,
                rate_from_salary=mio2_rate,
                amount_from_salary=mio2_amount,
                rate_on_salary=Decimal('0.000'),
                amount_on_salary=Decimal('0.00'),
            )
        )
    lines.append(
        PayslipContributionLine(
            tenant=payslip.tenant,
            payslip=payslip,
            name='DOP. ZA ZDRAVSTVENO OSIGURANJE',
            base_amount=payslip.gross_salary,
            rate_from_salary=Decimal('0.000'),
            amount_from_salary=Decimal('0.00'),
            rate_on_salary=zo_rate,
            amount_on_salary=zo_amount,
        )
    )
    PayslipContributionLine.objects.bulk_create(lines)
