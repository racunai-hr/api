"""Semantic contracts for TaxClassificationEngine (ADR-0019). No Django models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class Outcome(StrEnum):
    CLASSIFIED = 'CLASSIFIED'
    NOT_TAX_RELEVANT = 'NOT_TAX_RELEVANT'
    REVIEW_REQUIRED = 'REVIEW_REQUIRED'
    INVALID = 'INVALID'


class EventKind(StrEnum):
    ORIGINAL = 'original'
    ADVANCE = 'advance'
    REVERSAL = 'reversal'
    CORRECTION = 'correction'


class Direction(StrEnum):
    OUTPUT = 'output'
    INPUT = 'input'
    CORRECTIVE = 'corrective'


class PartnerProvenance(StrEnum):
    DOCUMENT_SNAPSHOT = 'document_snapshot'
    CURRENT_PARTNER = 'current_partner'
    LEDGER_RECONSTRUCTION = 'ledger_reconstruction'


class TaxRelevance(StrEnum):
    TAX_RELEVANT = 'TAX_RELEVANT'
    NOT_TAX_RELEVANT = 'NOT_TAX_RELEVANT'
    UNDETERMINED = 'UNDETERMINED'


class OriginTaxOwner(StrEnum):
    INVOICE = 'INVOICE'
    EXPENSE = 'EXPENSE'
    JOURNAL_LINE = 'JOURNAL_LINE'
    NONE = 'NONE'


class ClassifiedWithoutRowsError(RuntimeError):
    """CLASSIFIED must carry at least one proposed ledger row."""


@dataclass(frozen=True)
class PartnerSnapshot:
    name: str
    country: str
    tax_number: str
    vat_id: str
    provenance: PartnerProvenance


@dataclass(frozen=True)
class OriginTaxEffect:
    """Frozen tax effect of the original document, taken from existing ledger rows."""

    box: str
    base_amount: Decimal
    tax_amount: Decimal
    direction: Direction
    category: str
    ledger_type: str
    sign: Decimal
    source_kind: str
    source_document_id: int
    source_line_id: int | None
    ledger_entry_id: int | None


@dataclass(frozen=True)
class ProposedLedgerRow:
    source_kind: str
    source_document_id: int
    source_line_id: int | None
    event_kind: EventKind
    box: str
    category: str
    direction: Direction
    ledger_type: str
    base_amount: Decimal
    tax_amount: Decimal
    recognized_input_vat: Decimal
    unrecognized_input_vat: Decimal
    sign: Decimal
    rule_code: str
    period_year: int
    period_month: int
    mapping_version: int
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class TaxDocumentInput:
    tenant_id: int
    source_kind: str
    source_document_id: int
    source_line_id: int | None
    lifecycle_status: str
    event_kind: EventKind
    direction: Direction
    document_date: date
    supply_date: date | None
    partner: PartnerSnapshot | None
    base_amount: Decimal
    vat_rate: Decimal | None
    vat_amount: Decimal
    currency: str
    jurisdiction: str
    customer_type: str
    supply_kind: str
    declared_procedure: str
    originates_from: str | None
    origin_tax_effects: tuple[OriginTaxEffect, ...]
    origin_effects_ambiguous: bool
    tax_relevance: TaxRelevance
    origin_tax_owner: OriginTaxOwner
    has_linked_journal_entry: bool
    account_code: str | None
    debit_amount: Decimal
    credit_amount: Decimal
    description: str
    period_year: int
    period_month: int
    input_hash: str


@dataclass(frozen=True)
class TaxClassificationResult:
    outcome: Outcome
    reason: str
    rule_code: str
    rows: tuple[ProposedLedgerRow, ...]
    warnings: tuple[str, ...]
    input_hash: str
    mapping_version: int
    period_year: int
    period_month: int
    period_date_reason: str
    source_kind: str
    source_document_id: int
    source_line_id: int | None
