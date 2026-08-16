"""ZpPayload and related frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

ZP_SCHEMA_VERSION = '1.0'
ZP_MAPPING_VERSION = 1


@dataclass(frozen=True)
class ZpRow:
    country_code: str
    pdv_id: str
    goods_value: Decimal
    services_value: Decimal


@dataclass(frozen=True)
class ZpPreparedBy:
    first_name: str
    last_name: str
    email: str = ''
    phone: str = ''


@dataclass(frozen=True)
class ZpTaxpayer:
    name: str
    oib: str
    city: str
    street: str
    house_number: str


@dataclass(frozen=True)
class ZpPayload:
    period_from: date
    period_to: date
    taxpayer: ZpTaxpayer
    prepared_by: ZpPreparedBy
    tax_office_code: str
    rows: tuple[ZpRow, ...]
    schema_version: str = ZP_SCHEMA_VERSION
    mapping_version: int = ZP_MAPPING_VERSION

    @property
    def total_goods(self) -> Decimal:
        return sum((row.goods_value for row in self.rows), Decimal('0.00'))

    @property
    def total_services(self) -> Decimal:
        return sum((row.services_value for row in self.rows), Decimal('0.00'))
