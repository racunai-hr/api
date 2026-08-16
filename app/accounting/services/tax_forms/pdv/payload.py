"""Neutral PDV payload types — no XML dependency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

PDV_SCHEMA_VERSION = '11.0'

_MARGIN_BOX_CODES = frozenset({'701', '702', '703', '704'})

PdvFieldValue = 'PdvFieldPair | PdvMarginPair | Decimal | bool'


@dataclass(frozen=True)
class TaxpayerInfo:
    oib: str
    name: str
    street: str
    house_number: str
    city: str
    postal_code: str


@dataclass(frozen=True)
class PdvFieldPair:
    vrijednost: Decimal
    porez: Decimal


@dataclass(frozen=True)
class PdvMarginPair:
    nabavna_vrijednost: Decimal
    prodajna_vrijednost: Decimal


@dataclass(frozen=True)
class PdvFormHeader:
    tax_office_code: str
    prepared_by_first_name: str
    prepared_by_last_name: str


@dataclass(frozen=True)
class PdvPayload:
    schema_version: str
    mapping_version: int
    period_from: date
    period_to: date
    taxpayer: TaxpayerInfo
    fields: Mapping[str, PdvFieldPair | PdvMarginPair | Decimal | bool]

    def __post_init__(self) -> None:
        if not isinstance(self.fields, MappingProxyType):
            object.__setattr__(self, 'fields', MappingProxyType(dict(self.fields)))

    def __deepcopy__(self, memo):
        from copy import deepcopy

        return PdvPayload(
            schema_version=self.schema_version,
            mapping_version=self.mapping_version,
            period_from=deepcopy(self.period_from, memo),
            period_to=deepcopy(self.period_to, memo),
            taxpayer=deepcopy(self.taxpayer, memo),
            fields=freeze_fields({key: deepcopy(value, memo) for key, value in self.fields.items()}),
        )

    def field_pair(self, code: str) -> PdvFieldPair:
        value = self.fields.get(code)
        if isinstance(value, PdvFieldPair):
            return value
        return PdvFieldPair(Decimal('0.00'), Decimal('0.00'))

    def field_margin(self, code: str) -> PdvMarginPair:
        value = self.fields.get(code)
        if isinstance(value, PdvMarginPair):
            return value
        return PdvMarginPair(Decimal('0.00'), Decimal('0.00'))

    def field_scalar(self, code: str) -> Decimal:
        value = self.fields.get(code)
        if isinstance(value, Decimal):
            return value
        return Decimal('0.00')

    def field_bool(self, code: str) -> bool:
        value = self.fields.get(code)
        if isinstance(value, bool):
            return value
        return False

    def is_margin_box(self, code: str) -> bool:
        return code in _MARGIN_BOX_CODES


def freeze_fields(fields: dict[str, PdvFieldPair | PdvMarginPair | Decimal | bool]) -> MappingProxyType:
    return MappingProxyType(fields)
