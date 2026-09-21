"""Normalize address fields and assemble Tz2Payload (single pass)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from accounting.services.tax_forms.tz2.mapping import alma_2026_defaults, rates_for_year
from accounting.services.tax_forms.tz2.payload import (
    TZ2_MAPPING_VERSION,
    TZ2_SCHEMA_VERSION,
    Tz2Payload,
    Tz2PreparedBy,
    Tz2Taxpayer,
    money,
)


def normalize_house_number(raw: str) -> str:
    value = (raw or '').strip()
    if value.isdigit() and len(value) <= 4:
        return value.zfill(4)
    return value[:10]


def normalize_municipality_code(raw: str) -> str:
    digits = ''.join(ch for ch in (raw or '') if ch.isdigit())
    if len(digits) > 3:
        digits = digits[-3:]
    return digits.zfill(3) if digits else ''


def normalize_place(raw: str) -> str:
    return (raw or '').strip().upper()


@dataclass(frozen=True)
class Tz2BuildInput:
    first_name: str
    last_name: str
    oib: str
    municipality_code: str
    city: str
    street: str
    house_number: str
    prepared_by_first_name: str
    prepared_by_last_name: str
    room_beds: int = 0
    aux_beds: int = 0
    camp_units: int = 0
    robinson_units: int = 0
    opg_room_beds: int = 0
    opg_aux_beds: int = 0
    opg_camp_units: int = 0
    opg_robinson_units: int = 0
    room_bed_rate: Decimal | None = None
    aux_bed_rate: Decimal | None = None
    camp_rate: Decimal | None = None
    robinson_rate: Decimal | None = None
    opg_room_bed_rate: Decimal | None = None
    opg_aux_bed_rate: Decimal | None = None
    opg_camp_rate: Decimal | None = None
    opg_robinson_rate: Decimal | None = None
    discount_group_1: Decimal = Decimal('0.00')
    discount_group_2: Decimal = Decimal('0.00')
    discount_group_3: Decimal = Decimal('0.00')
    discount_group_4: Decimal = Decimal('0.00')
    payment_installments: bool = False
    ep_receipts: Decimal = Decimal('0.00')


def _rate(override: Decimal | None, default: Decimal) -> Decimal:
    if override is None:
        return default
    return money(override)


def aggregate_tz2(tax_year: int, build_input: Tz2BuildInput) -> Tz2Payload:
    """One pass: defaults + input → immutable Tz2Payload."""
    rates = rates_for_year(tax_year)
    return Tz2Payload(
        period_from=date(tax_year, 1, 1),
        period_to=date(tax_year, 12, 31),
        taxpayer=Tz2Taxpayer(
            first_name=build_input.first_name.strip(),
            last_name=build_input.last_name.strip(),
            oib=build_input.oib.strip(),
            municipality_code=normalize_municipality_code(build_input.municipality_code),
            city=normalize_place(build_input.city),
            street=normalize_place(build_input.street),
            house_number=normalize_house_number(build_input.house_number),
        ),
        prepared_by=Tz2PreparedBy(
            first_name=build_input.prepared_by_first_name.strip(),
            last_name=build_input.prepared_by_last_name.strip(),
        ),
        room_beds=int(build_input.room_beds),
        aux_beds=int(build_input.aux_beds),
        camp_units=int(build_input.camp_units),
        robinson_units=int(build_input.robinson_units),
        opg_room_beds=int(build_input.opg_room_beds),
        opg_aux_beds=int(build_input.opg_aux_beds),
        opg_camp_units=int(build_input.opg_camp_units),
        opg_robinson_units=int(build_input.opg_robinson_units),
        room_bed_rate=_rate(build_input.room_bed_rate, rates['room_bed_rate']),
        aux_bed_rate=_rate(build_input.aux_bed_rate, rates['aux_bed_rate']),
        camp_rate=_rate(build_input.camp_rate, rates['camp_rate']),
        robinson_rate=_rate(build_input.robinson_rate, rates['robinson_rate']),
        opg_room_bed_rate=_rate(build_input.opg_room_bed_rate, rates['opg_room_bed_rate']),
        opg_aux_bed_rate=_rate(build_input.opg_aux_bed_rate, rates['opg_aux_bed_rate']),
        opg_camp_rate=_rate(build_input.opg_camp_rate, rates['opg_camp_rate']),
        opg_robinson_rate=_rate(build_input.opg_robinson_rate, rates['opg_robinson_rate']),
        discount_group_1=money(build_input.discount_group_1),
        discount_group_2=money(build_input.discount_group_2),
        discount_group_3=money(build_input.discount_group_3),
        discount_group_4=money(build_input.discount_group_4),
        payment_installments=bool(build_input.payment_installments),
        ep_receipts=money(build_input.ep_receipts),
        schema_version=TZ2_SCHEMA_VERSION,
        mapping_version=TZ2_MAPPING_VERSION,
    )


def alma_2026_input(*, prepared_by_first_name: str, prepared_by_last_name: str) -> Tz2BuildInput:
    defaults = alma_2026_defaults()
    return Tz2BuildInput(
        first_name=defaults['first_name'],
        last_name=defaults['last_name'],
        oib=defaults['oib'],
        municipality_code=defaults['municipality_code'],
        city=defaults['city'],
        street=defaults['street'],
        house_number=defaults['house_number'],
        prepared_by_first_name=prepared_by_first_name,
        prepared_by_last_name=prepared_by_last_name,
        room_beds=defaults['room_beds'],
        aux_beds=defaults['aux_beds'],
        camp_units=defaults['camp_units'],
        robinson_units=defaults['robinson_units'],
        opg_room_beds=defaults['opg_room_beds'],
        opg_aux_beds=defaults['opg_aux_beds'],
        opg_camp_units=defaults['opg_camp_units'],
        opg_robinson_units=defaults['opg_robinson_units'],
        payment_installments=defaults['payment_installments'],
        ep_receipts=defaults['ep_receipts'],
        discount_group_1=defaults['discount_group_1'],
        discount_group_2=defaults['discount_group_2'],
        discount_group_3=defaults['discount_group_3'],
        discount_group_4=defaults['discount_group_4'],
    )
