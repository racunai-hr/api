"""Agregacija podataka za rekapitulacije i knjiženje plaće (čita obračunate Payslip-ove)."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

from payroll.models import PayrollPeriod


@dataclass
class EarningTotal:
    name: str
    hours: Decimal
    gross_amount: Decimal


@dataclass
class ContributionTotal:
    name: str
    amount_from_salary: Decimal
    amount_on_salary: Decimal

    @property
    def total(self) -> Decimal:
        return self.amount_from_salary + self.amount_on_salary


@dataclass
class PeriodTotals:
    period: PayrollPeriod
    employee_count: int
    document_count: int
    earnings: list[EarningTotal]
    contributions: list[ContributionTotal]
    gross_total: Decimal
    income_base_total: Decimal
    personal_allowance_total: Decimal
    tax_base_total: Decimal
    income_tax_total: Decimal
    surtax_total: Decimal
    net_total: Decimal
    adjustments_total: Decimal
    zo_total: Decimal
    total_labor_cost: Decimal
    protected_total: Decimal
    regular_account_total: Decimal


def aggregate_period_totals(period: PayrollPeriod) -> PeriodTotals:
    payslips = list(
        period.payslips.all().prefetch_related('earning_lines', 'contribution_lines', 'adjustment_lines')
    )

    earnings_by_name: OrderedDict[str, EarningTotal] = OrderedDict()
    contributions_by_name: OrderedDict[str, ContributionTotal] = OrderedDict()

    zero = Decimal('0.00')
    gross_total = income_base_total = personal_allowance_total = Decimal('0.00')
    tax_base_total = income_tax_total = surtax_total = net_total = Decimal('0.00')
    adjustments_total = zo_total = total_labor_cost = Decimal('0.00')
    protected_total = regular_account_total = Decimal('0.00')

    for payslip in payslips:
        gross_total += payslip.gross_salary
        income_base_total += payslip.income_base
        personal_allowance_total += payslip.personal_allowance_amount
        tax_base_total += payslip.tax_base
        income_tax_total += payslip.income_tax_amount
        surtax_total += payslip.surtax_amount
        net_total += payslip.net_salary
        zo_total += payslip.zo_amount
        total_labor_cost += payslip.total_labor_cost
        protected_total += payslip.protected_total
        regular_account_total += payslip.regular_account_amount

        for line in payslip.earning_lines.all():
            entry = earnings_by_name.setdefault(line.name, EarningTotal(line.name, zero, zero))
            entry.hours += line.hours
            entry.gross_amount += line.gross_amount

        for line in payslip.contribution_lines.all():
            entry = contributions_by_name.setdefault(
                line.name, ContributionTotal(line.name, zero, zero)
            )
            entry.amount_from_salary += line.amount_from_salary
            entry.amount_on_salary += line.amount_on_salary

        for line in payslip.adjustment_lines.all():
            adjustments_total += line.amount

    return PeriodTotals(
        period=period,
        employee_count=len({p.employee_id for p in payslips}),
        document_count=len(payslips),
        earnings=list(earnings_by_name.values()),
        contributions=list(contributions_by_name.values()),
        gross_total=gross_total,
        income_base_total=income_base_total,
        personal_allowance_total=personal_allowance_total,
        tax_base_total=tax_base_total,
        income_tax_total=income_tax_total,
        surtax_total=surtax_total,
        net_total=net_total,
        adjustments_total=adjustments_total,
        zo_total=zo_total,
        total_labor_cost=total_labor_cost,
        protected_total=protected_total,
        regular_account_total=regular_account_total,
    )


@dataclass
class JournalLine:
    account_code: str
    account_name: str
    description: str
    debit: Decimal
    credit: Decimal


# Zadane šifre konta (RRIF-RP2025) — koriste se kad tenant nema (još) seedane
# PostingRule redove za 'payroll_posted' (npr. tenant nastao prije ove značajke).
# Account name je samo za prikaz u PDF-u; stvarni konto se traži u ChartOfAccounts.
#
# Napomena (e2e usporedba s uzorkom "plaća obračun.pdf", FINE STAR d.o.o., 2022.):
# stari FINE STAR software je trošak doprinosa iz plaće (MIO) I trošak doprinosa
# na plaću (ZO) knjižio na ISTI konto 4220 "Doprinosi na plaće". Ovaj modul po
# RRIF-RP2025 kontnom planu razdvaja to na dva konta (4220 za MIO iz plaće, 4230
# za ZO na plaću) jer je to ispravnije po trenutnom RRIF planu. Ako neki tenant
# treba točno kopirati staru (FINE STAR 2022.) numeraciju, to se postiže kroz
# admin-editable PostingRule (document_type='payroll_posted') bez izmjene koda.
_DEFAULT_ACCOUNT_CODES = {
    'payroll_net_total': ('4200', 'Troškovi neto plaća uprave i prodaje', '2300', 'Obveze za neto-plaće'),
    'payroll_income_tax_total': ('4210', 'Troškovi poreza iz plaća', '2410', 'Obveze za porez na dohodak iz plaća'),
    'payroll_mio_from_salary_total': ('4220', 'Troškovi doprinosa iz plaća', '2420', 'Doprinos za MO iz plaća'),
    'payroll_mio1': ('4220', 'Troškovi doprinosa iz plaća', '2420', 'Doprinos za MIO I'),
    'payroll_mio2': ('4220', 'Troškovi doprinosa iz plaća', '2421', 'Doprinos za MIO II'),
    'payroll_deduction_liability': ('4200', 'Troškovi neto plaća uprave i prodaje', '2306', 'Obveze za obustave iz neto-plaća'),
    'payroll_zo_total': ('4230', 'Doprinosi na plaće', '2423', 'Doprinos za zdravstveno osiguranje na plaće'),
}


def _account_codes_for_tenant(tenant) -> dict:
    """Konto šifre po amount_field, iz tenantovog PostingRule (admin-podesivo), s fallbackom."""
    from accounting.models import PostingRule

    codes = dict(_DEFAULT_ACCOUNT_CODES)
    rules = PostingRule.all_objects.filter(
        tenant=tenant, document_type='payroll_posted', is_active=True, amount_field__in=codes.keys(),
    )
    for rule in rules:
        _debit_default, debit_name, _credit_default, credit_name = codes[rule.amount_field]
        codes[rule.amount_field] = (
            rule.debit_account_code, debit_name, rule.credit_account_code, credit_name,
        )
    return codes


def build_payroll_journal_lines(period: PayrollPeriod) -> list[JournalLine]:
    """Stavke knjiženja plaće (RRIF-RP2025 konta) — koristi se i za PDF i za posting.

    Za razliku od starog softvera iz uzorka (koji je sve doprinose gurao u jedan
    konto 4220), ovo koristi ispravnu RRIF-RP2025 razdiobu:
    4200 trošak neto, 4210 trošak poreza iz plaće, 4220 trošak doprinosa iz
    plaće (MIO), 4230 doprinosi na plaću (ZO) — knjiženo naspram obveza
    2300/2410/2420(+2421)/2423. Konto šifre dolaze iz tenantovog PostingRule
    (document_type='payroll_posted'), podesive u Django adminu.
    """
    totals = aggregate_period_totals(period)
    desc = f'Plaća za {period.month}/{period.year}'
    account_codes = _account_codes_for_tenant(period.tenant)
    lines: list[JournalLine] = []

    mio1 = sum((p.mio1_amount for p in period.payslips.all()), Decimal('0.00'))
    mio2 = sum((p.mio2_amount for p in period.payslips.all()), Decimal('0.00'))
    deductions = sum((p.deductions_total for p in period.payslips.all()), Decimal('0.00'))
    from accounting.models import PostingRule
    has_split = PostingRule.all_objects.filter(
        tenant=period.tenant, document_type='payroll_posted', is_active=True, amount_field='payroll_mio1',
    ).exists()
    amounts = {
        'payroll_net_total': totals.net_total,
        'payroll_income_tax_total': totals.income_tax_total,
        'payroll_zo_total': totals.zo_total,
    }
    if has_split:
        amounts['payroll_mio1'] = mio1
        amounts['payroll_mio2'] = mio2
    else:
        amounts['payroll_mio_from_salary_total'] = mio1 + mio2
    if deductions:
        amounts['payroll_deduction_liability'] = deductions
        amounts['payroll_net_total'] = totals.net_total - deductions

    for field, amount in amounts.items():
        if not amount:
            continue
        debit_code, debit_name, credit_code, credit_name = account_codes[field]
        lines.append(JournalLine(debit_code, debit_name, desc, amount, Decimal('0.00')))
        lines.append(JournalLine(credit_code, credit_name, desc, Decimal('0.00'), amount))

    return lines
