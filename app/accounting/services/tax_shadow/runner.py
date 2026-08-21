"""Read-only shadow runner. Never writes VATLedgerEntry."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType

from accounting.models import VATLedgerEntry, VATPeriod
from accounting.services.tax_forms.pdv.mapping import (
    EU_GOODS_ASSET_ACCOUNTS,
    EU_GOODS_OBVEZA_ACCOUNTS,
    EU_GOODS_PRETPOREZ_ACCOUNTS,
    IOSS_PRETPOREZ_ACCOUNTS,
)
from accounting.services.tax_shadow.adapters import (
    adapt_expense,
    adapt_invoice_header,
    adapt_invoice_item,
    adapt_journal_line,
    adapt_reversal_entry,
)
from accounting.services.tax_shadow.allowlist import apply_allowlist
from accounting.services.tax_shadow.diff import diff_multisets
from accounting.services.tax_shadow.selection import (
    select_expenses,
    select_invoices,
    select_posted_journal_lines,
    select_reversal_entries,
)
from domains.tax.classification.contracts import Outcome, TaxClassificationResult
from domains.tax.classification.engine import classify, reconcile_rc_bases
from domains.tax.classification.finalize import finalize_period

_JOURNAL_INPUT_ACCOUNTS = frozenset({'1400'})
_JOURNAL_OUTPUT_ACCOUNT_TO_BOX = {'240010': '201', '240011': '202', '24001': '203'}
_RC_SKIP_ACCOUNTS = (
    EU_GOODS_ASSET_ACCOUNTS
    | EU_GOODS_PRETPOREZ_ACCOUNTS
    | EU_GOODS_OBVEZA_ACCOUNTS
    | IOSS_PRETPOREZ_ACCOUNTS
)


@dataclass
class ShadowReport:
    period_id: int
    tenant: str
    year: int
    month: int
    stale: bool
    fingerprint_before: str
    fingerprint_after: str
    outcomes: dict[str, int]
    expected: list[dict]
    unexpected: list[dict]
    warnings: list[dict]
    invalid: int
    review_required: int

    def to_json(self) -> str:
        payload = {
            'period_id': self.period_id,
            'tenant': self.tenant,
            'year': self.year,
            'month': self.month,
            'stale': self.stale,
            'fingerprint_before': self.fingerprint_before,
            'fingerprint_after': self.fingerprint_after,
            'outcomes': dict(sorted(self.outcomes.items())),
            'expected': sorted(self.expected, key=lambda item: (item['code'], item['source_kind'], item['source_document_id'])),
            'unexpected': sorted(self.unexpected, key=lambda item: (item.get('kind', ''), item.get('source_kind', ''), item.get('source_document_id', 0), item.get('box', ''))),
            'warnings': sorted(self.warnings, key=lambda item: (item['type'], item.get('source_document_id', 0))),
            'invalid': self.invalid,
            'review_required': self.review_required,
        }
        return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n'

    def exit_code(self) -> int:
        if self.stale or self.invalid or self.unexpected:
            return 1
        if self.review_required and not self.expected:
            return 1
        leftover_review = self.review_required - sum(1 for item in self.expected if item['code'] in {
            'EXPENSE_GENERIC_303_REMOVED',
            'UNKNOWN_RATE_NO_LONGER_SKIPPED',
            'UNMAPPED_TAX_ACCOUNT_NO_LONGER_SKIPPED',
        })
        if leftover_review > 0:
            return 1
        return 0


def ledger_fingerprint(period: VATPeriod) -> str:
    rows = list(
        VATLedgerEntry.all_objects.filter(vat_period=period)
        .order_by('pk')
        .values_list('pk', 'vat_box', 'base_amount', 'vat_amount', 'source_content_type_id', 'source_object_id')
    )
    encoded = json.dumps(
        [
            [pk, box, format(base, 'f'), format(vat, 'f'), ct, oid]
            for pk, box, base, vat, ct, oid in rows
        ],
        separators=(',', ':'),
    )
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def input_snapshot_hash(period: VATPeriod) -> str:
    invoices = select_invoices(period)
    expenses = select_expenses(period)
    lines = select_posted_journal_lines(period)
    reversals = select_reversal_entries(period)
    payload = {
        'invoices': sorted((inv.pk, inv.updated_at.isoformat(), inv.status) for inv in invoices),
        'expenses': sorted((exp.pk, exp.updated_at.isoformat(), exp.status) for exp in expenses),
        'lines': sorted((line.pk, line.journal_entry.status, str(line.debit_amount), str(line.credit_amount)) for line in lines),
        'reversals': sorted((entry.pk, entry.status, entry.reversed_entry_id) for entry in reversals),
        'ledger': ledger_fingerprint(period),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _skip_auto_posted_line(line, *, expense_ct_id, invoice_ct_id) -> bool:
    entry = line.journal_entry
    account_code = line.account.account_code
    source_ct_id = entry.source_content_type_id
    if source_ct_id == expense_ct_id and account_code in _JOURNAL_INPUT_ACCOUNTS:
        return True
    if source_ct_id == expense_ct_id and account_code in IOSS_PRETPOREZ_ACCOUNTS:
        return True
    if source_ct_id == expense_ct_id and account_code in (
        EU_GOODS_ASSET_ACCOUNTS | EU_GOODS_PRETPOREZ_ACCOUNTS | EU_GOODS_OBVEZA_ACCOUNTS
    ):
        return True
    if source_ct_id == expense_ct_id and account_code in _RC_SKIP_ACCOUNTS:
        return True
    if source_ct_id == invoice_ct_id and account_code in _JOURNAL_OUTPUT_ACCOUNT_TO_BOX:
        return True
    return False


def shadow_classify_period(period: VATPeriod) -> ShadowReport:
    from expenses.models import Expense
    from invoices.models import Invoice

    started = input_snapshot_hash(period)
    fingerprint_before = ledger_fingerprint(period)

    expense_ct = ContentType.objects.get_for_model(Expense)
    invoice_ct = ContentType.objects.get_for_model(Invoice)

    documents = []
    warnings = []

    for invoice in select_invoices(period):
        items = list(invoice.items.all())
        if items:
            for item in items:
                documents.append(adapt_invoice_item(item, period=period))
        elif invoice.subtotal > 0:
            documents.append(adapt_invoice_header(invoice, period=period))

    for expense in select_expenses(period):
        documents.append(adapt_expense(expense, period=period))

    for line in select_posted_journal_lines(period):
        if _skip_auto_posted_line(line, expense_ct_id=expense_ct.id, invoice_ct_id=invoice_ct.id):
            continue
        documents.append(adapt_journal_line(line, period=period))

    for reversal in select_reversal_entries(period):
        documents.append(adapt_reversal_entry(reversal, period=period))

    results: list[TaxClassificationResult] = []
    classified_rows = []
    for document in documents:
        result = classify(document)
        results.append(result)
        classified_rows.extend(result.rows)
        for warning in result.warnings:
            warnings.append({
                'type': warning,
                'source_kind': result.source_kind,
                'source_document_id': result.source_document_id,
            })

    finalized = finalize_period(
        reconcile_rc_bases(tuple(classified_rows)),
        period_year=period.year,
        period_month=period.month,
        period_id=period.pk,
    )

    ledger_entries = list(
        VATLedgerEntry.all_objects.filter(vat_period=period).select_related('source_content_type')
    )
    diffs, _review_unused = diff_multisets(
        results=results,
        finalized_rows=finalized,
        ledger_entries=ledger_entries,
    )
    expected_hits, leftover_diffs, leftover_outcomes = apply_allowlist(results=results, diffs=diffs)

    ended = input_snapshot_hash(period)
    fingerprint_after = ledger_fingerprint(period)
    stale = started != ended

    outcomes = Counter(result.outcome.value for result in results)
    expected_payload = [
        {
            'code': hit.code,
            'source_kind': hit.source_kind,
            'source_document_id': hit.source_document_id,
        }
        for hit in expected_hits
    ]
    unexpected = []
    for diff in leftover_diffs:
        unexpected.append({
            'kind': diff.kind,
            'source_kind': diff.source_kind,
            'source_document_id': diff.source_document_id,
            'box': diff.key.box,
            'shadow_count': diff.shadow_count,
            'ledger_count': diff.ledger_count,
        })
    for result in leftover_outcomes:
        unexpected.append({
            'kind': 'outcome_mismatch',
            'source_kind': result.source_kind,
            'source_document_id': result.source_document_id,
            'box': '',
            'outcome': result.outcome.value,
            'reason': result.reason,
        })

    return ShadowReport(
        period_id=period.pk,
        tenant=period.tenant.slug,
        year=period.year,
        month=period.month,
        stale=stale,
        fingerprint_before=fingerprint_before,
        fingerprint_after=fingerprint_after,
        outcomes=dict(outcomes),
        expected=expected_payload,
        unexpected=unexpected,
        warnings=warnings,
        invalid=sum(1 for result in leftover_outcomes if result.outcome == Outcome.INVALID),
        review_required=sum(1 for result in results if result.outcome == Outcome.REVIEW_REQUIRED),
    )
