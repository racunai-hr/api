"""ObrazacPDVispravak v1.0 payload.

This form notifies the tax office that a tax base was reduced because a
receivable stayed unpaid for more than a year. It is not a new ObrazacPDV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

SCHEMA_VERSION = '1.0'


@dataclass(frozen=True)
class PdvIspravakTaxpayer:
    name: str
    oib: str
    city: str
    street: str
    house_number: str
    phone: str
    email: str


@dataclass(frozen=True)
class PdvIspravakPreparer:
    first_name: str
    last_name: str
    place: str
    prepared_on: date


@dataclass(frozen=True)
class PdvIspravakRow:
    invoice_number: str
    invoice_date: date
    identifier_kind: int
    identifier: str
    customer_name: str
    base_5: Decimal
    vat_5: Decimal
    base_13: Decimal
    vat_13: Decimal
    base_25: Decimal
    vat_25: Decimal

    @property
    def total_base(self) -> Decimal:
        return self.base_5 + self.base_13 + self.base_25

    @property
    def total_vat(self) -> Decimal:
        return self.vat_5 + self.vat_13 + self.vat_25


@dataclass(frozen=True)
class PdvIspravakPayload:
    period_from: date
    period_to: date
    taxpayer: PdvIspravakTaxpayer
    preparer: PdvIspravakPreparer
    rows: tuple[PdvIspravakRow, ...]

    @property
    def total_base_5(self) -> Decimal:
        return sum((row.base_5 for row in self.rows), Decimal('0.00'))

    @property
    def total_vat_5(self) -> Decimal:
        return sum((row.vat_5 for row in self.rows), Decimal('0.00'))

    @property
    def total_base_13(self) -> Decimal:
        return sum((row.base_13 for row in self.rows), Decimal('0.00'))

    @property
    def total_vat_13(self) -> Decimal:
        return sum((row.vat_13 for row in self.rows), Decimal('0.00'))

    @property
    def total_base_25(self) -> Decimal:
        return sum((row.base_25 for row in self.rows), Decimal('0.00'))

    @property
    def total_vat_25(self) -> Decimal:
        return sum((row.vat_25 for row in self.rows), Decimal('0.00'))

    @property
    def total_base(self) -> Decimal:
        return self.total_base_5 + self.total_base_13 + self.total_base_25

    @property
    def total_vat(self) -> Decimal:
        return self.total_vat_5 + self.total_vat_13 + self.total_vat_25
