"""JoppdPayload and related frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

JOPPD_SCHEMA_VERSION = '1.0-draft'  # vidi napomenu u domains/tax/forms/joppd/__init__.py
JOPPD_MAPPING_VERSION = 1


@dataclass(frozen=True)
class JoppdEmployer:
    name: str
    oib: str
    street: str
    house_number: str
    city: str
    postal_code: str


@dataclass(frozen=True)
class JoppdPreparedBy:
    first_name: str
    last_name: str
    email: str = ''
    phone: str = ''


@dataclass(frozen=True)
class JoppdRow:
    recipient_oib: str
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
class JoppdPayload:
    period_year: int
    period_month: int
    payment_date: date
    employer: JoppdEmployer
    prepared_by: JoppdPreparedBy
    rows: tuple[JoppdRow, ...]
    schema_version: str = JOPPD_SCHEMA_VERSION
    mapping_version: int = JOPPD_MAPPING_VERSION

    @property
    def report_id(self) -> str:
        """Oznaka izvještaja YYDDD prema datumu isplate."""
        day = self.payment_date.timetuple().tm_yday
        return f'{self.payment_date.year % 100:02d}{day:03d}'

    @property
    def total_gross(self) -> Decimal:
        return sum((row.gross_salary for row in self.rows), Decimal('0.00'))

    @property
    def total_mio1(self) -> Decimal:
        return sum((row.mio1_amount for row in self.rows), Decimal('0.00'))

    @property
    def total_mio2(self) -> Decimal:
        return sum((row.mio2_amount for row in self.rows), Decimal('0.00'))

    @property
    def total_income_tax(self) -> Decimal:
        return sum((row.income_tax_amount for row in self.rows), Decimal('0.00'))

    @property
    def total_net(self) -> Decimal:
        return sum((row.net_salary for row in self.rows), Decimal('0.00'))

    @property
    def total_zo(self) -> Decimal:
        return sum((row.zo_amount for row in self.rows), Decimal('0.00'))
