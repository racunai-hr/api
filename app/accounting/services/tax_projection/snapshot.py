"""Consistent read snapshot for VAT projection prepare."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from accounting.models import JournalEntry, JournalEntryLine, VATLedgerEntry, VATLedgerOrigin, VATPeriod
from accounting.services.tax_forms.pdv.mapping import (
    EU_GOODS_ASSET_ACCOUNTS,
    EU_GOODS_OBVEZA_ACCOUNTS,
    EU_GOODS_PRETPOREZ_ACCOUNTS,
    IOSS_PRETPOREZ_ACCOUNTS,
    PDV_MAPPING_VERSION,
    VIII1_SUB_BOXES,
)
from accounting.services.tax_projection.contracts import PROJECTION_ENGINE_VERSION
from accounting.services.tax_shadow.adapters import (
    adapt_expense,
    adapt_invoice_header,
    adapt_invoice_item,
    adapt_journal_line,
    adapt_reversal_entry,
)
from accounting.services.tax_shadow.selection import (
    select_expenses,
    select_invoices,
    select_posted_journal_lines,
    select_reversal_entries,
)
from domains.tax.classification.contracts import TaxDocumentInput

_JOURNAL_INPUT_ACCOUNTS = frozenset({'1400'})
_JOURNAL_OUTPUT_ACCOUNT_TO_BOX = {'240010': '201', '240011': '202', '24001': '203'}
_RC_SKIP_ACCOUNTS = (
    EU_GOODS_ASSET_ACCOUNTS
    | EU_GOODS_PRETPOREZ_ACCOUNTS
    | EU_GOODS_OBVEZA_ACCOUNTS
    | IOSS_PRETPOREZ_ACCOUNTS
)


@dataclass
class ProjectionSnapshot:
    period: VATPeriod
    invoices: list
    expenses: list
    lines: list[JournalEntryLine]
    reversals: list[JournalEntry]
    manuals_viii1: list[VATLedgerEntry]
    manuals_610: list[VATLedgerEntry]
    expense_ct_id: int
    invoice_ct_id: int


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


def load_snapshot(period: VATPeriod) -> ProjectionSnapshot:
    from expenses.models import Expense
    from invoices.models import Invoice

    period = VATPeriod.all_objects.select_related('tenant').get(pk=period.pk)
    manuals = VATLedgerEntry.all_objects.filter(vat_period=period).filter(
        Q(is_manual=True) | Q(origin=VATLedgerOrigin.MANUAL)
    )
    return ProjectionSnapshot(
        period=period,
        invoices=sorted(select_invoices(period), key=lambda invoice: invoice.pk),
        expenses=sorted(select_expenses(period), key=lambda expense: expense.pk),
        lines=sorted(select_posted_journal_lines(period), key=lambda line: line.pk),
        reversals=sorted(select_reversal_entries(period), key=lambda entry: entry.pk),
        manuals_viii1=list(manuals.filter(vat_box__in=VIII1_SUB_BOXES).order_by('pk')),
        manuals_610=list(manuals.filter(vat_box='610').order_by('pk')),
        expense_ct_id=ContentType.objects.get_for_model(Expense).id,
        invoice_ct_id=ContentType.objects.get_for_model(Invoice).id,
    )


def documents_from_snapshot(snapshot: ProjectionSnapshot) -> list[TaxDocumentInput]:
    documents: list[TaxDocumentInput] = []
    period = snapshot.period
    for invoice in snapshot.invoices:
        items = sorted(invoice.items.all(), key=lambda item: item.pk)
        if items:
            for item in items:
                documents.append(adapt_invoice_item(item, period=period))
        elif invoice.subtotal > 0:
            documents.append(adapt_invoice_header(invoice, period=period))
    for expense in snapshot.expenses:
        documents.append(adapt_expense(expense, period=period))
    for line in snapshot.lines:
        if _skip_auto_posted_line(
            line,
            expense_ct_id=snapshot.expense_ct_id,
            invoice_ct_id=snapshot.invoice_ct_id,
        ):
            continue
        documents.append(adapt_journal_line(line, period=period))
    for reversal in snapshot.reversals:
        documents.append(adapt_reversal_entry(reversal, period=period))
    return documents


def snapshot_fingerprint(snapshot: ProjectionSnapshot, documents: list[TaxDocumentInput]) -> str:
    payload = {
        'tenant_id': snapshot.period.tenant_id,
        'period_id': snapshot.period.pk,
        'year': snapshot.period.year,
        'month': snapshot.period.month,
        'engine_version': PROJECTION_ENGINE_VERSION,
        'mapping_version': PDV_MAPPING_VERSION,
        'documents': sorted(document.input_hash for document in documents),
        'manuals_viii1': [
            [
                entry.pk,
                entry.vat_box,
                format(entry.base_amount, 'f'),
                format(entry.vat_amount, 'f'),
                entry.document_number,
            ]
            for entry in snapshot.manuals_viii1
        ],
        'manuals_610': [entry.pk for entry in snapshot.manuals_610],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
