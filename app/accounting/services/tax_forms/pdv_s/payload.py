"""Dataclasses for Obrazac PDV-S v1.0."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


PDV_S_SCHEMA_VERSION = '1.0'


@dataclass(frozen=True)
class PdvSRow:
    country_code: str
    pdv_id: str
    goods_value: Decimal
    services_value: Decimal


@dataclass(frozen=True)
class PdvSPreparedBy:
    first_name: str
    last_name: str
    email: str = ''
    phone: str = ''


@dataclass(frozen=True)
class PdvSTaxpayer:
    name: str
    oib: str
    city: str
    street: str
    house_number: str


@dataclass(frozen=True)
class PdvSPayload:
    period_from: date
    period_to: date
    taxpayer: PdvSTaxpayer
    prepared_by: PdvSPreparedBy
    tax_office_code: str
    rows: tuple[PdvSRow, ...]

    @property
    def total_goods(self) -> Decimal:
        return sum((row.goods_value for row in self.rows), Decimal('0.00'))

    @property
    def total_services(self) -> Decimal:
        return sum((row.services_value for row in self.rows), Decimal('0.00'))
