"""Narrow expected-divergence allow-list. Anything else is unexpected."""

from __future__ import annotations

from dataclasses import dataclass

from domains.tax.classification.contracts import Outcome, TaxClassificationResult
from accounting.services.tax_shadow.diff import DiffRecord

ALLOWED_CODES = frozenset({
    'EXPENSE_GENERIC_303_REMOVED',
    'UNKNOWN_RATE_NO_LONGER_SKIPPED',
    'UNMAPPED_TAX_ACCOUNT_NO_LONGER_SKIPPED',
    'REVERSAL_TAX_EFFECT_RESTORED',
})


@dataclass(frozen=True)
class ExpectedHit:
    code: str
    source_kind: str
    source_document_id: int


def apply_allowlist(
    *,
    results: list[TaxClassificationResult],
    diffs: list[DiffRecord],
) -> tuple[list[ExpectedHit], list[DiffRecord], list[TaxClassificationResult]]:
    """Consume diffs/outcomes that match exact allow-list rules.

    Remaining diffs and remaining REVIEW/INVALID results are unexpected
    (except NOT_TAX_RELEVANT, which is not a divergence).
    """
    hits: list[ExpectedHit] = []
    consumed_diff_indexes: set[int] = set()
    consumed_results: set[int] = set()

    for index, result in enumerate(results):
        code = result.rule_code
        if code == 'EXPENSE_GENERIC_303_REMOVED' and result.outcome == Outcome.REVIEW_REQUIRED:
            doc_id = _result_doc_id(result, 'expense')
            matching = [
                i for i, diff in enumerate(diffs)
                if i not in consumed_diff_indexes
                and diff.source_kind == 'expense'
                and diff.source_document_id == doc_id
                and diff.kind == 'extra_row'
                and diff.key.box == '303'
            ]
            if len(matching) == 1:
                consumed_diff_indexes.add(matching[0])
                consumed_results.add(index)
                hits.append(ExpectedHit(code, 'expense', doc_id))
        elif code == 'UNKNOWN_RATE_NO_LONGER_SKIPPED' and result.outcome == Outcome.REVIEW_REQUIRED:
            if not result.rows:
                consumed_results.add(index)
                hits.append(ExpectedHit(code, 'invoice', _result_doc_id(result, 'invoice')))
        elif code == 'UNMAPPED_TAX_ACCOUNT_NO_LONGER_SKIPPED' and result.outcome == Outcome.REVIEW_REQUIRED:
            consumed_results.add(index)
            hits.append(ExpectedHit(code, 'journal_line', _result_doc_id(result, 'journal_line')))
        elif code == 'REV_INVERT' and result.outcome == Outcome.CLASSIFIED:
            doc_id = _result_doc_id(result, 'journal_line')
            matching = [
                i for i, diff in enumerate(diffs)
                if i not in consumed_diff_indexes
                and diff.source_kind == 'journal_line'
                and diff.source_document_id == doc_id
                and diff.kind == 'missing_row'
                and diff.key.sign != 0
            ]
            if matching:
                for i in matching:
                    consumed_diff_indexes.add(i)
                consumed_results.add(index)
                hits.append(ExpectedHit('REVERSAL_TAX_EFFECT_RESTORED', 'journal_line', doc_id))

    leftover_diffs = [diff for i, diff in enumerate(diffs) if i not in consumed_diff_indexes]
    leftover_outcomes = [
        result for i, result in enumerate(results)
        if i not in consumed_results and result.outcome in {Outcome.REVIEW_REQUIRED, Outcome.INVALID}
    ]
    return hits, leftover_diffs, leftover_outcomes


def _result_doc_id(result: TaxClassificationResult, kind: str) -> int:
    if kind == 'invoice' and result.source_kind in {'invoice', 'invoice_item'}:
        return result.source_document_id
    if result.source_kind == kind or (kind == 'invoice' and result.source_kind == 'invoice_item'):
        return result.source_document_id
    return result.source_document_id
