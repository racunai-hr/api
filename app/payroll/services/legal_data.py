"""Učitavanje platformskih zakonskih referentnih podataka za obračun plaće.

NAPOMENA: brojke za 2026. preuzete su u studenom 2026. iz javno dostupnih izvora
(Porezna uprava — osobni odbitak, stope doprinosa; RRiF i racunovodstvo.hr —
stope poreza na dohodak po JLS; Narodne novine 132/2025 — minimalna plaća;
DZS/Fina — referentna neto plaća za zaštićeni račun). Prije stvarne produkcijske
uporabe potrebna je potvrda ovlaštenog računovođe / provjera uz Narodne novine.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.db import transaction

from datetime import date

from payroll.models import MunicipalityIncomeTaxRate, PayrollLegalParameter, PayrollTaxYear

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
DEFAULT_MUNICIPALITY_CSV_2026 = DATA_DIR / 'municipality_income_tax_rates_2026.csv'


@dataclass(frozen=True)
class PayrollTaxYearDefaults:
    year: int
    basic_personal_allowance: Decimal = Decimal('600.00')
    mio1_rate: Decimal = Decimal('15.00')
    mio2_rate: Decimal = Decimal('5.00')
    mio1_only_rate: Decimal = Decimal('20.00')
    zo_rate: Decimal = Decimal('16.50')
    mio1_base_reduction_threshold: Decimal = Decimal('1300.00')
    minimum_wage_gross: Decimal = Decimal('1050.00')
    protected_account_reference_net: Decimal = Decimal('1434.00')
    income_tax_annual_bracket_threshold: Decimal = Decimal('60000.00')


# Poznate godine — 2026. je jedina trenutno popunjena verificiranim izvorima
# (vidi gornju napomenu). Dodati nove godine ovdje kad se objave nove brojke.
KNOWN_YEARS = {
    2026: PayrollTaxYearDefaults(year=2026),
}


def ensure_payroll_tax_year(year: int) -> PayrollTaxYear:
    """Seed/update PayrollTaxYear za poznatu godinu. Ne radi ništa za nepoznate godine."""
    defaults = KNOWN_YEARS.get(year)
    if defaults is None:
        raise ValueError(
            f'Nema poznatih zakonskih parametara plaće za godinu {year}. '
            f'Unesite ih ručno kroz PayrollTaxYear ili dodajte u KNOWN_YEARS.'
        )
    obj, _created = PayrollTaxYear.objects.update_or_create(
        year=year,
        defaults={
            'basic_personal_allowance': defaults.basic_personal_allowance,
            'mio1_rate': defaults.mio1_rate,
            'mio2_rate': defaults.mio2_rate,
            'mio1_only_rate': defaults.mio1_only_rate,
            'zo_rate': defaults.zo_rate,
            'mio1_base_reduction_threshold': defaults.mio1_base_reduction_threshold,
            'minimum_wage_gross': defaults.minimum_wage_gross,
            'protected_account_reference_net': defaults.protected_account_reference_net,
            'income_tax_annual_bracket_threshold': defaults.income_tax_annual_bracket_threshold,
        },
    )
    _seed_legal_parameters(obj)
    return obj


def _seed_legal_parameters(tax_year: PayrollTaxYear) -> None:
    """Upiši parametre za godinu. effective_basis je po šifri, ne jedan globalni default."""
    valid_from = date(tax_year.year, 1, 1)
    valid_to = date(tax_year.year, 12, 31)
    rows = {
        'MIO1_RATE': (tax_year.mio1_rate, PayrollLegalParameter.PAYMENT_DATE),
        'MIO2_RATE': (tax_year.mio2_rate, PayrollLegalParameter.PAYMENT_DATE),
        'MIO1_ONLY_RATE': (tax_year.mio1_only_rate, PayrollLegalParameter.PAYMENT_DATE),
        'ZO_RATE': (tax_year.zo_rate, PayrollLegalParameter.PAYMENT_DATE),
        'PERSONAL_ALLOWANCE_BASE': (tax_year.basic_personal_allowance, PayrollLegalParameter.PAYMENT_DATE),
        'TAX_BRACKET_ANNUAL': (tax_year.income_tax_annual_bracket_threshold, PayrollLegalParameter.PAYMENT_DATE),
        'PROTECTED_REFERENCE_NET': (tax_year.protected_account_reference_net, PayrollLegalParameter.PAYMENT_DATE),
        'MIN_WAGE_GROSS_FULLTIME': (tax_year.minimum_wage_gross, PayrollLegalParameter.PAYROLL_MONTH),
        'MIO1_REDUCTION_CEILING': (tax_year.mio1_base_reduction_threshold, PayrollLegalParameter.PAYROLL_MONTH),
        'MIO1_REDUCTION_FLOOR': (Decimal('700.00'), PayrollLegalParameter.PAYROLL_MONTH),
        'MIO1_REDUCTION_FLAT': (Decimal('300.00'), PayrollLegalParameter.PAYROLL_MONTH),
    }
    for code, (value, basis) in rows.items():
        PayrollLegalParameter.objects.update_or_create(
            code=code,
            valid_from=valid_from,
            defaults={
                'value': value,
                'valid_to': valid_to,
                'effective_basis': basis,
                'source_reference': 'PayrollTaxYear seed',
            },
        )


def import_municipality_rates(year: int, csv_path: Path | str | None = None) -> tuple[int, int]:
    """Uvezi stope poreza na dohodak po JLS iz CSV-a (code;name;lower_rate;higher_rate)."""
    path = Path(csv_path) if csv_path else DEFAULT_MUNICIPALITY_CSV_2026
    created = 0
    updated = 0
    with path.open(encoding='utf-8') as f, transaction.atomic():
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            _obj, was_created = MunicipalityIncomeTaxRate.objects.update_or_create(
                year=year,
                code=row['code'].strip(),
                defaults={
                    'name': row['name'].strip(),
                    'lower_rate': Decimal(row['lower_rate'].strip()),
                    'higher_rate': Decimal(row['higher_rate'].strip()),
                    'valid_from': date(year, 1, 1),
                    'valid_to': date(year, 12, 31),
                    'effective_basis': PayrollLegalParameter.PAYMENT_DATE,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
    return created, updated
