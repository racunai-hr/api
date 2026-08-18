"""Read-only VAT projection prepare. Never writes VATLedgerEntry or VATProjectionRun."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from decimal import Decimal

from django.db import OperationalError, connection, transaction

from accounting.models import VATLedgerEntry, VATPeriod
from accounting.services.tax_forms.pdv.mapping import (
    PDV_MAPPING,
    PDV_MAPPING_VERSION,
    VIII1_SUB_BOXES,
    VIII1_VAT_AGGREGATE_BOXES,
)
from accounting.services.tax_projection.contracts import (
    PROJECTION_ENGINE_VERSION,
    ProjectedLedgerRow,
    ProjectionIssue,
    VatProjectionCandidate,
    VatProjectionStatus,
)
from accounting.services.tax_projection.identity import identity_of, to_projected
from accounting.services.tax_projection.snapshot import (
    ProjectionSnapshot,
    documents_from_snapshot,
    load_snapshot,
    snapshot_fingerprint,
)
from domains.tax.classification.contracts import (
    ClassifiedWithoutRowsError,
    Direction,
    EventKind,
    Outcome,
    ProposedLedgerRow,
    TaxClassificationResult,
)
from domains.tax.classification.engine import classify, reconcile_rc_bases
from domains.tax.classification.finalize import finalize_period


def prepare_vat_projection(period: VATPeriod) -> VatProjectionCandidate:
    """Build a projection candidate from a consistent snapshot. Zero ORM writes."""
    outermost = not connection.in_atomic_block
    try:
        with transaction.atomic():
            if outermost:
                _set_repeatable_read()
            return _prepare_in_snapshot(period)
    except OperationalError as exc:
        if _is_serialization_failure(exc):
            return _stale_candidate(period, input_fingerprint='', output_fingerprint='')
        raise


def _set_repeatable_read() -> None:
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')


def _is_serialization_failure(exc: BaseException) -> bool:
    text = str(exc).lower()
    return 'could not serialize' in text or 'serializationfailure' in text


def _prepare_in_snapshot(period: VATPeriod) -> VatProjectionCandidate:
    snapshot = load_snapshot(period)
    documents = documents_from_snapshot(snapshot)
    fingerprint_before = snapshot_fingerprint(snapshot, documents)
    candidate = _build_candidate(snapshot, documents, fingerprint_before)

    snapshot_after = load_snapshot(period)
    documents_after = documents_from_snapshot(snapshot_after)
    fingerprint_after = snapshot_fingerprint(snapshot_after, documents_after)
    if fingerprint_before != fingerprint_after:
        return _stale_candidate(
            snapshot.period,
            input_fingerprint=fingerprint_before,
            output_fingerprint='',
        )
    return candidate


def _build_candidate(
    snapshot: ProjectionSnapshot,
    documents,
    input_fingerprint: str,
) -> VatProjectionCandidate:
    period = snapshot.period
    issues: list[ProjectionIssue] = []
    results: list[TaxClassificationResult] = []
    classified_rows: list[ProposedLedgerRow] = []

    for document in documents:
        try:
            result = classify(document)
        except ClassifiedWithoutRowsError:
            issues.append(
                ProjectionIssue(
                    code='CLASSIFIED_WITHOUT_ROWS',
                    source_kind=document.source_kind,
                    source_id=document.source_document_id,
                    source_line_id=document.source_line_id,
                    detail='classified_without_rows',
                )
            )
            continue
        results.append(result)
        classified_rows.extend(result.rows)
        if result.outcome in (Outcome.REVIEW_REQUIRED, Outcome.INVALID):
            issues.append(
                ProjectionIssue(
                    code=result.rule_code or result.reason,
                    source_kind=result.source_kind,
                    source_id=result.source_document_id,
                    source_line_id=result.source_line_id,
                    detail=result.reason,
                )
            )
        elif result.outcome == Outcome.CLASSIFIED and not result.rows:
            issues.append(
                ProjectionIssue(
                    code='CLASSIFIED_WITHOUT_ROWS',
                    source_kind=result.source_kind,
                    source_id=result.source_document_id,
                    source_line_id=result.source_line_id,
                    detail='classified_without_rows',
                )
            )
        elif result.outcome != Outcome.CLASSIFIED and result.rows:
            issues.append(
                ProjectionIssue(
                    code='ROWS_ON_NON_CLASSIFIED',
                    source_kind=result.source_kind,
                    source_id=result.source_document_id,
                    source_line_id=result.source_line_id,
                    detail=result.outcome.value,
                )
            )

    for entry in snapshot.manuals_610:
        issues.append(
            ProjectionIssue(
                code='MANUAL_610_CONFLICT',
                source_kind='manual_ledger',
                source_id=entry.pk,
                source_line_id=None,
                detail='manual_610_present',
            )
        )

    generated = reconcile_rc_bases(tuple(classified_rows))
    manual_rows = tuple(
        _manual_viii1_row(entry, period=period) for entry in snapshot.manuals_viii1
    )
    finalized = finalize_period(
        generated + manual_rows,
        period_year=period.year,
        period_month=period.month,
        period_id=period.pk,
    )
    projected = tuple(to_projected(row) for row in finalized)
    issues.extend(_row_issues(projected, period_id=period.pk))
    issues = _sorted_issues(issues)

    status = VatProjectionStatus.REJECTED if issues else VatProjectionStatus.READY
    writable = status == VatProjectionStatus.READY and period.status == 'open'
    counts = Counter(result.outcome.value for result in results)
    return VatProjectionCandidate(
        period_id=period.pk,
        status=status,
        writable=writable,
        input_fingerprint=input_fingerprint,
        output_fingerprint=_output_fingerprint(projected),
        mapping_version=PDV_MAPPING_VERSION,
        engine_version=PROJECTION_ENGINE_VERSION,
        rows=projected,
        issues=tuple(issues),
        primary_rejection_code=issues[0].code if issues else '',
        classified_count=counts.get(Outcome.CLASSIFIED.value, 0),
        not_relevant_count=counts.get(Outcome.NOT_TAX_RELEVANT.value, 0),
        review_count=counts.get(Outcome.REVIEW_REQUIRED.value, 0),
        invalid_count=counts.get(Outcome.INVALID.value, 0),
    )


def _manual_viii1_row(entry: VATLedgerEntry, *, period: VATPeriod) -> ProposedLedgerRow:
    rule = PDV_MAPPING[entry.vat_box]
    if entry.vat_box in VIII1_VAT_AGGREGATE_BOXES:
        base = Decimal('0.00')
        tax = entry.vat_amount or Decimal('0.00')
        signed = tax
    else:
        base = entry.base_amount or Decimal('0.00')
        tax = Decimal('0.00')
        signed = base
    sign = Decimal('1') if signed >= 0 else Decimal('-1')
    return ProposedLedgerRow(
        source_kind='manual_ledger',
        source_document_id=entry.pk,
        source_line_id=None,
        event_kind=EventKind.ORIGINAL,
        box=entry.vat_box,
        category=entry.entry_category or rule.entry_category,
        direction=Direction.INPUT,
        ledger_type=entry.ledger_type or rule.ledger_type,
        base_amount=abs(base),
        tax_amount=abs(tax),
        recognized_input_vat=Decimal('0.00'),
        unrecognized_input_vat=Decimal('0.00'),
        sign=sign,
        rule_code=f'MANUAL_{entry.vat_box}',
        period_year=period.year,
        period_month=period.month,
        mapping_version=PDV_MAPPING_VERSION,
        outputs=('pdv', 'control_ura'),
    )


def _row_issues(rows: tuple[ProjectedLedgerRow, ...], *, period_id: int) -> list[ProjectionIssue]:
    issues: list[ProjectionIssue] = []
    seen_identity: dict[tuple, ProjectedLedgerRow] = {}
    for row in rows:
        if row.box not in PDV_MAPPING:
            issues.append(
                ProjectionIssue(
                    code='UNKNOWN_BOX',
                    source_kind=row.source_kind,
                    source_id=row.source_id,
                    source_line_id=row.source_line_id,
                    detail='unknown_box',
                )
            )
        key = identity_of(row)
        if key in seen_identity:
            issues.append(
                ProjectionIssue(
                    code='DUPLICATE_ROW_IDENTITY',
                    source_kind=row.source_kind,
                    source_id=row.source_id,
                    source_line_id=row.source_line_id,
                    detail='duplicate_row_identity',
                )
            )
        else:
            seen_identity[key] = row

    boxes_610 = [row for row in rows if row.box == '610']
    if len(boxes_610) > 1:
        issues.append(
            ProjectionIssue(
                code='DUPLICATE_610',
                source_kind='vat_period',
                source_id=period_id,
                source_line_id=None,
                detail='duplicate_610',
            )
        )
    subtotal = Decimal('0.00')
    for row in rows:
        if row.box not in VIII1_SUB_BOXES:
            continue
        amount = row.tax_amount if row.box in VIII1_VAT_AGGREGATE_BOXES else row.base_amount
        subtotal += amount * row.sign
    if subtotal != 0 and len(boxes_610) == 0:
        issues.append(
            ProjectionIssue(
                code='MISSING_610',
                source_kind='vat_period',
                source_id=period_id,
                source_line_id=None,
                detail='missing_610',
            )
        )
    return issues


def _sorted_issues(issues: list[ProjectionIssue]) -> list[ProjectionIssue]:
    return sorted(
        issues,
        key=lambda issue: (
            issue.code,
            issue.source_kind,
            issue.source_id,
            issue.source_line_id if issue.source_line_id is not None else 0,
        ),
    )


def _output_fingerprint(rows: tuple[ProjectedLedgerRow, ...]) -> str:
    payload = [
        {
            'identity': list(identity_of(row)),
            'box': row.box,
            'base': format(row.base_amount, 'f'),
            'tax': format(row.tax_amount, 'f'),
            'sign': format(row.sign, 'f'),
            'rule_code': row.rule_code,
        }
        for row in sorted(rows, key=lambda row: (*identity_of(row), row.box))
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _stale_candidate(
    period: VATPeriod,
    *,
    input_fingerprint: str,
    output_fingerprint: str,
) -> VatProjectionCandidate:
    issue = ProjectionIssue(
        code='STALE_PROJECTION',
        source_kind='vat_period',
        source_id=period.pk,
        source_line_id=None,
        detail='input_set_changed',
    )
    return VatProjectionCandidate(
        period_id=period.pk,
        status=VatProjectionStatus.STALE,
        writable=False,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        mapping_version=PDV_MAPPING_VERSION,
        engine_version=PROJECTION_ENGINE_VERSION,
        rows=(),
        issues=(issue,),
        primary_rejection_code='STALE_PROJECTION',
        classified_count=0,
        not_relevant_count=0,
        review_count=0,
        invalid_count=0,
    )
