"""Projection DTOs. READY is not write authorization."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

PROJECTION_ENGINE_VERSION = 1


class VatProjectionStatus(StrEnum):
    READY = 'READY'
    REJECTED = 'REJECTED'
    STALE = 'STALE'


@dataclass(frozen=True)
class ProjectionIssue:
    code: str
    source_kind: str
    source_id: int
    source_line_id: int | None
    detail: str


@dataclass(frozen=True)
class ProjectedLedgerRow:
    source_kind: str
    source_id: int
    source_line_kind: str
    source_line_id: int | None
    row_role: str
    event_kind: str
    box: str
    category: str
    direction: str
    ledger_type: str
    base_amount: Decimal
    tax_amount: Decimal
    sign: Decimal
    rule_code: str
    mapping_version: int


@dataclass(frozen=True)
class VatProjectionCandidate:
    """Prepare outcome. writable is informational; apply uses locked period.status."""

    period_id: int
    tenant_id: int
    status: VatProjectionStatus
    writable: bool
    input_fingerprint: str
    output_fingerprint: str
    mapping_version: int
    engine_version: int
    rows: tuple[ProjectedLedgerRow, ...]
    issues: tuple[ProjectionIssue, ...]
    primary_rejection_code: str
    classified_count: int
    not_relevant_count: int
    review_count: int
    invalid_count: int
