"""Tz2Payload and related frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

TZ2_SCHEMA_VERSION = '1.0'
TZ2_MAPPING_VERSION = 1

_MONEY = Decimal('0.01')
_THREE = Decimal('3')


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(_MONEY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Tz2Taxpayer:
    first_name: str
    last_name: str
    oib: str
    municipality_code: str
    city: str
    street: str
    house_number: str


@dataclass(frozen=True)
class Tz2PreparedBy:
    first_name: str
    last_name: str


@dataclass(frozen=True)
class Tz2Payload:
    period_from: date
    period_to: date
    taxpayer: Tz2Taxpayer
    prepared_by: Tz2PreparedBy
    room_beds: int
    aux_beds: int
    camp_units: int
    robinson_units: int
    opg_room_beds: int
    opg_aux_beds: int
    opg_camp_units: int
    opg_robinson_units: int
    room_bed_rate: Decimal
    aux_bed_rate: Decimal
    camp_rate: Decimal
    robinson_rate: Decimal
    opg_room_bed_rate: Decimal
    opg_aux_bed_rate: Decimal
    opg_camp_rate: Decimal
    opg_robinson_rate: Decimal
    discount_group_1: Decimal
    discount_group_2: Decimal
    discount_group_3: Decimal
    discount_group_4: Decimal
    payment_installments: bool
    ep_receipts: Decimal
    schema_version: str = TZ2_SCHEMA_VERSION
    mapping_version: int = TZ2_MAPPING_VERSION

    @property
    def room_bed_total(self) -> Decimal:
        return money(Decimal(self.room_beds) * self.room_bed_rate)

    @property
    def aux_bed_total(self) -> Decimal:
        return money(Decimal(self.aux_beds) * self.aux_bed_rate)

    @property
    def camp_total(self) -> Decimal:
        return money(Decimal(self.camp_units) * self.camp_rate)

    @property
    def robinson_total(self) -> Decimal:
        return money(Decimal(self.robinson_units) * self.robinson_rate)

    @property
    def opg_room_bed_total(self) -> Decimal:
        return money(Decimal(self.opg_room_beds) * self.opg_room_bed_rate)

    @property
    def opg_aux_bed_total(self) -> Decimal:
        return money(Decimal(self.opg_aux_beds) * self.opg_aux_bed_rate)

    @property
    def opg_camp_total(self) -> Decimal:
        return money(Decimal(self.opg_camp_units) * self.opg_camp_rate)

    @property
    def opg_robinson_total(self) -> Decimal:
        return money(Decimal(self.opg_robinson_units) * self.opg_robinson_rate)

    @property
    def total_assessed(self) -> Decimal:
        return money(
            self.room_bed_total
            + self.aux_bed_total
            + self.camp_total
            + self.robinson_total
            + self.opg_room_bed_total
            + self.opg_aux_bed_total
            + self.opg_camp_total
            + self.opg_robinson_total
        )

    @property
    def total_discount(self) -> Decimal:
        return money(
            self.discount_group_1
            + self.discount_group_2
            + self.discount_group_3
            + self.discount_group_4
        )

    @property
    def amount_after_discount(self) -> Decimal:
        return money(self.total_assessed - self.total_discount)

    @property
    def lump_sum_flag(self) -> str:
        return '0' if self.payment_installments else '1'

    @property
    def lump_sum_amount(self) -> Decimal:
        return Decimal('0.00') if self.payment_installments else self.amount_after_discount

    @property
    def installment_flag(self) -> str:
        return '1' if self.payment_installments else '0'

    @property
    def installment_amount(self) -> Decimal:
        if not self.payment_installments:
            return Decimal('0.00')
        return money(self.amount_after_discount / _THREE)
