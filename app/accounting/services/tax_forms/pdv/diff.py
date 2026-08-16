"""Structured comparison of PdvPayload field maps."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from accounting.services.tax_forms.pdv.payload import (
    PdvFieldPair,
    PdvMarginPair,
    PdvPayload,
)

_TWO_PLACES = Decimal('0.01')


class PdvFieldDifferenceKind(StrEnum):
    VALUE_CHANGED = 'value_changed'
    MISSING_IN_EXPECTED = 'missing_in_expected'
    MISSING_IN_ACTUAL = 'missing_in_actual'
    TYPE_CHANGED = 'type_changed'


KIND_SORT_ORDER = (
    PdvFieldDifferenceKind.MISSING_IN_EXPECTED,
    PdvFieldDifferenceKind.MISSING_IN_ACTUAL,
    PdvFieldDifferenceKind.TYPE_CHANGED,
    PdvFieldDifferenceKind.VALUE_CHANGED,
)


@dataclass(frozen=True)
class PdvFieldDifference:
    field: str
    kind: PdvFieldDifferenceKind
    expected: str
    actual: str

    @property
    def is_structural(self) -> bool:
        return self.kind != PdvFieldDifferenceKind.VALUE_CHANGED

    @property
    def is_value_difference(self) -> bool:
        return not self.is_structural


def _decimal_str(value: Decimal) -> str:
    return str(value.quantize(_TWO_PLACES))


def _value_type_name(value: object) -> str:
    if isinstance(value, PdvFieldPair):
        return 'pair'
    if isinstance(value, PdvMarginPair):
        return 'margin'
    if isinstance(value, Decimal):
        return 'scalar'
    if isinstance(value, bool):
        return 'bool'
    return type(value).__name__


def _expand_field(code: str, value: object) -> dict[str, str]:
    if isinstance(value, PdvFieldPair):
        return {
            f'{code}.vrijednost': _decimal_str(value.vrijednost),
            f'{code}.porez': _decimal_str(value.porez),
        }
    if isinstance(value, PdvMarginPair):
        return {
            f'{code}.nabavna_vrijednost': _decimal_str(value.nabavna_vrijednost),
            f'{code}.prodajna_vrijednost': _decimal_str(value.prodajna_vrijednost),
        }
    if isinstance(value, Decimal):
        return {code: _decimal_str(value)}
    if isinstance(value, bool):
        return {code: str(value).lower()}
    return {code: str(value)}


def _sort_differences(differences: list[PdvFieldDifference]) -> list[PdvFieldDifference]:
    kind_rank = {kind: index for index, kind in enumerate(KIND_SORT_ORDER)}
    return sorted(differences, key=lambda item: (kind_rank[item.kind], item.field))


def compare_pdv_payload_fields(
    expected: PdvPayload,
    actual: PdvPayload,
) -> list[PdvFieldDifference]:
    """Return sorted field differences; empty list means a match on business fields."""
    differences: list[PdvFieldDifference] = []
    expected_codes = set(expected.fields.keys())
    actual_codes = set(actual.fields.keys())

    for code in sorted(expected_codes | actual_codes, key=int):
        expected_value = expected.fields.get(code)
        actual_value = actual.fields.get(code)

        if expected_value is None:
            differences.append(
                PdvFieldDifference(
                    field=code,
                    kind=PdvFieldDifferenceKind.MISSING_IN_EXPECTED,
                    expected='',
                    actual=_value_type_name(actual_value),
                ),
            )
            continue
        if actual_value is None:
            differences.append(
                PdvFieldDifference(
                    field=code,
                    kind=PdvFieldDifferenceKind.MISSING_IN_ACTUAL,
                    expected=_value_type_name(expected_value),
                    actual='',
                ),
            )
            continue
        if type(expected_value) is not type(actual_value):
            differences.append(
                PdvFieldDifference(
                    field=code,
                    kind=PdvFieldDifferenceKind.TYPE_CHANGED,
                    expected=_value_type_name(expected_value),
                    actual=_value_type_name(actual_value),
                ),
            )
            continue

        expected_paths = _expand_field(code, expected_value)
        actual_paths = _expand_field(code, actual_value)
        for path in sorted(set(expected_paths) | set(actual_paths)):
            expected_text = expected_paths.get(path)
            actual_text = actual_paths.get(path)
            if expected_text is None:
                differences.append(
                    PdvFieldDifference(
                        field=path,
                        kind=PdvFieldDifferenceKind.MISSING_IN_EXPECTED,
                        expected='',
                        actual=actual_text or '',
                    ),
                )
            elif actual_text is None:
                differences.append(
                    PdvFieldDifference(
                        field=path,
                        kind=PdvFieldDifferenceKind.MISSING_IN_ACTUAL,
                        expected=expected_text,
                        actual='',
                    ),
                )
            elif expected_text != actual_text:
                differences.append(
                    PdvFieldDifference(
                        field=path,
                        kind=PdvFieldDifferenceKind.VALUE_CHANGED,
                        expected=expected_text,
                        actual=actual_text,
                    ),
                )

    return _sort_differences(differences)


class PayloadMismatchError(Exception):
    """Raised when imported signed XML payload differs from the ERP draft."""

    def __init__(self, differences: list[PdvFieldDifference]) -> None:
        self.differences = differences
        super().__init__(self.summary())

    @property
    def changed_fields(self) -> list[str]:
        return [difference.field for difference in self.differences]

    @property
    def structural_differences(self) -> list[PdvFieldDifference]:
        return [difference for difference in self.differences if difference.is_structural]

    @property
    def value_differences(self) -> list[PdvFieldDifference]:
        return [difference for difference in self.differences if difference.is_value_difference]

    def summary(self) -> str:
        structural_count = len(self.structural_differences)
        value_count = len(self.value_differences)
        parts: list[str] = []

        if structural_count:
            if structural_count == 1:
                parts.append('1 strukturna razlika')
            else:
                parts.append(f'{structural_count} strukturne razlike')

        if value_count:
            if value_count == 1:
                parts.append('1 razlika u vrijednostima')
            else:
                parts.append(f'{value_count} razlike u vrijednostima')

        if not parts:
            return 'Nema razlika'
        return ', '.join(parts)
