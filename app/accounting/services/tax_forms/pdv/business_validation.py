"""Business validation rules for Obrazac PDV ↔ PDV-S (V001–V005)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from accounting.models import VATPeriod
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from accounting.services.tax_forms.pdv.payload import PdvFieldPair
from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows

Severity = Literal['fatal', 'warning', 'info']


@dataclass(frozen=True)
class BusinessValidationIssue:
    rule_id: str
    severity: Severity
    message: str


class PdvBusinessValidationError(Exception):
    """Raised when a Fatal business rule is violated."""

    def __init__(self, issues: tuple[BusinessValidationIssue, ...]) -> None:
        self.issues = issues
        super().__init__(issues[0].message if issues else 'PDV business validation failed')


def _pair_base(payload, code: str) -> Decimal:
    field = payload.fields.get(code)
    if isinstance(field, PdvFieldPair):
        return field.vrijednost
    return Decimal('0.00')


def validate_v002_pdvs_total_not_exceed_pdv(period: VATPeriod) -> BusinessValidationIssue | None:
    """PDV-S(13) ≤ II.5 + II.6 + II.7 (osnovice)."""
    pdv = build_pdv_payload(period)
    pdv_s = aggregate_pdv_s_rows(period)

    pdv_base_ii = (
        _pair_base(pdv, '205')
        + _pair_base(pdv, '206')
        + _pair_base(pdv, '207')
    ).quantize(Decimal('0.01'))
    pdv_s_goods = pdv_s.total_goods.quantize(Decimal('0.01'))

    if pdv_s_goods > pdv_base_ii:
        return BusinessValidationIssue(
            rule_id='V002',
            severity='fatal',
            message=(
                f'PDV-S ukupno stečena dobra ({pdv_s_goods}) '
                f'premašuje zbroj osnovica II.5+II.6+II.7 ({pdv_base_ii}).'
            ),
        )
    return None


def validate_pdv_business_rules(
    period: VATPeriod,
    *,
    severity_filter: frozenset[Severity] | None = None,
) -> list[BusinessValidationIssue]:
    """Run business validators; default returns all issues."""
    issues: list[BusinessValidationIssue] = []
    v002 = validate_v002_pdvs_total_not_exceed_pdv(period)
    if v002 is not None:
        issues.append(v002)

    if severity_filter is not None:
        issues = [issue for issue in issues if issue.severity in severity_filter]
    return issues


def assert_pdv_business_rules(period: VATPeriod) -> None:
    """Raise PdvBusinessValidationError on any Fatal rule violation."""
    fatal = validate_pdv_business_rules(period, severity_filter=frozenset({'fatal'}))
    if fatal:
        raise PdvBusinessValidationError(tuple(fatal))
