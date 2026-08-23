"""Read-only document / GL / subledger integrity audit (Finance domain).

Detects legacy mismatches between business documents, posted journal entries,
and SubledgerItem/Allocation SSOT. Patterns are NOT mutually exclusive.
P8 applies only when no other mismatch pattern matches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from django.contrib.contenttypes.models import ContentType

from accounting.models import JournalEntry, JournalEntryLine, SubledgerAllocation, SubledgerItem
from accounting.services.journal_markers import extract_document_type
from banking.models import BankTransaction
from expenses.models import Expense
from invoices.models import Invoice

# Pattern codes (non-exclusive except P8).
P1_CANCELLED_SUBLEDGER_PAID_DOC = 'P1_cancelled_subledger_paid_doc'
P2_MANUAL_JE_NO_SUBLEDGER = 'P2_manual_je_no_subledger'
P3_GL_BYPASS_AP_CANDIDATE = 'P3_gl_bypass_ap_candidate'
P4_SUBLEDGER_REVERSED_OBLIGATION_JE = 'P4_subledger_reversed_obligation_je'
P5_ORPHAN_SUBLEDGER_WRONG_PARTNER = 'P5_orphan_subledger_wrong_partner'
P6_BANK_SETTLED_NO_ALLOCATION = 'P6_bank_settled_no_allocation'
P7_DUPLICATE_ACTIVE_SUBLEDGER = 'P7_duplicate_active_subledger'
P8_STANDARD_PATH_OK = 'P8_standard_path_ok'

PATTERN_SEVERITY: dict[str, str] = {
    P1_CANCELLED_SUBLEDGER_PAID_DOC: 'high',
    P2_MANUAL_JE_NO_SUBLEDGER: 'medium',
    P3_GL_BYPASS_AP_CANDIDATE: 'high',
    P4_SUBLEDGER_REVERSED_OBLIGATION_JE: 'high',
    P5_ORPHAN_SUBLEDGER_WRONG_PARTNER: 'medium',
    P6_BANK_SETTLED_NO_ALLOCATION: 'high',
    P7_DUPLICATE_ACTIVE_SUBLEDGER: 'high',
    P8_STANDARD_PATH_OK: 'info',
}

EXPENSE_SCAN_STATUSES = frozenset({'approved', 'paid'})
INVOICE_SCAN_STATUSES = frozenset({'sent', 'paid', 'overdue'})

PARTNER_PAYABLE_BASES = frozenset({'2201'})
PARTNER_RECEIVABLE_BASES = frozenset({'1201'})
CASH_BASES = frozenset({'1000', '1020'})


def _account_base(account_code: str | None) -> str:
    if not account_code:
        return ''
    return account_code.split('-', 1)[0]


def _money_str(value: Decimal | None) -> str:
    if value is None:
        return '0.00'
    return f'{value:.2f}'


def _je_summary(je: JournalEntry) -> dict[str, Any]:
    return {
        'id': je.pk,
        'entry_number': je.entry_number,
        'entry_date': je.entry_date.isoformat() if je.entry_date else None,
        'status': je.status,
        'document_type_marker': extract_document_type(je.description or ''),
        'description': (je.description or '')[:200],
        'has_2201': False,
        'has_1201': False,
    }


def _lines_for_journal_entries(journal_entry_ids: Iterable[int]) -> dict[int, list[JournalEntryLine]]:
    if not journal_entry_ids:
        return {}
    lines_by_je: dict[int, list[JournalEntryLine]] = {}
    qs = JournalEntryLine.objects.filter(journal_entry_id__in=journal_entry_ids).select_related(
        'account',
        'analytic_account',
    )
    for line in qs:
        lines_by_je.setdefault(line.journal_entry_id, []).append(line)
    return lines_by_je


def _annotate_je_accounts(je_summaries: dict[int, dict], lines_by_je: dict[int, list[JournalEntryLine]]) -> None:
    for je_id, summary in je_summaries.items():
        for line in lines_by_je.get(je_id, []):
            base = _account_base(line.account.account_code)
            if base in PARTNER_PAYABLE_BASES:
                summary['has_2201'] = True
            if base in PARTNER_RECEIVABLE_BASES:
                summary['has_1201'] = True


def _collect_journal_entries(tenant, *, content_type, object_id: int) -> list[JournalEntry]:
    return list(
        JournalEntry.all_objects.filter(
            tenant=tenant,
            source_content_type=content_type,
            source_object_id=object_id,
        ).order_by('entry_date', 'pk')
    )


def _collect_subledger_items(tenant, *, content_type, object_id: int) -> list[SubledgerItem]:
    return list(
        SubledgerItem.all_objects.filter(
            tenant=tenant,
            source_content_type=content_type,
            source_object_id=object_id,
        ).select_related('partner', 'journal_entry')
        .order_by('pk')
    )


def _subledger_item_dict(item: SubledgerItem) -> dict[str, Any]:
    obligation_je = item.journal_entry
    return {
        'id': item.pk,
        'partner_id': item.partner_id,
        'partner_name': item.partner.name if item.partner_id else None,
        'status': item.status,
        'original_amount': _money_str(item.original_amount),
        'open_amount': _money_str(item.open_amount),
        'obligation_journal_entry_id': obligation_je.pk if obligation_je else None,
        'obligation_journal_entry_status': obligation_je.status if obligation_je else None,
    }


@dataclass
class DocumentPostingContext:
    tenant_slug: str
    document_type: str
    document_id: int
    document_number: str
    document_status: str
    partner_id: int | None
    partner_name: str | None
    journal_entries: list[JournalEntry] = field(default_factory=list)
    subledger_items: list[SubledgerItem] = field(default_factory=list)
    allocations: list[SubledgerAllocation] = field(default_factory=list)
    bank_matches: list[BankTransaction] = field(default_factory=list)
    lines_by_je: dict[int, list[JournalEntryLine]] = field(default_factory=dict)

    @property
    def active_subledger_items(self) -> list[SubledgerItem]:
        return [item for item in self.subledger_items if item.status != 'cancelled']

    @property
    def canonical_active_subledger_items(self) -> list[SubledgerItem]:
        if not self.partner_id:
            return []
        return [item for item in self.active_subledger_items if item.partner_id == self.partner_id]

    @property
    def posted_journal_entries(self) -> list[JournalEntry]:
        return [je for je in self.journal_entries if je.status == 'posted']

    @property
    def is_paid_like(self) -> bool:
        if self.document_type == 'expense':
            return self.document_status == 'paid'
        if self.document_type == 'invoice':
            return self.document_status == 'paid'
        return False

    @property
    def is_posted_like(self) -> bool:
        if self.document_type == 'expense':
            return self.document_status in EXPENSE_SCAN_STATUSES
        if self.document_type == 'invoice':
            return self.document_status in INVOICE_SCAN_STATUSES
        return False


def collect_expense_posting_context(tenant, expense: Expense) -> DocumentPostingContext:
    ct = ContentType.objects.get_for_model(Expense)
    journal_entries = _collect_journal_entries(tenant, content_type=ct, object_id=expense.pk)
    subledger_items = _collect_subledger_items(tenant, content_type=ct, object_id=expense.pk)
    je_ids = [je.pk for je in journal_entries]
    lines_by_je = _lines_for_journal_entries(je_ids)
    allocations = list(
        SubledgerAllocation.all_objects.filter(
            tenant=tenant,
            subledger_item_id__in=[item.pk for item in subledger_items],
        ).select_related('journal_entry', 'subledger_item')
    )
    bank_matches = list(
        BankTransaction.all_objects.filter(
            tenant=tenant,
            matched_journal_entry_id__in=je_ids,
        ).order_by('transaction_date', 'pk')
    )
    partner = expense.supplier
    return DocumentPostingContext(
        tenant_slug=tenant.slug,
        document_type='expense',
        document_id=expense.pk,
        document_number=expense.expense_number,
        document_status=expense.status,
        partner_id=partner.pk if partner else None,
        partner_name=partner.name if partner else None,
        journal_entries=journal_entries,
        subledger_items=subledger_items,
        allocations=allocations,
        bank_matches=bank_matches,
        lines_by_je=lines_by_je,
    )


def collect_invoice_posting_context(tenant, invoice: Invoice) -> DocumentPostingContext:
    ct = ContentType.objects.get_for_model(Invoice)
    journal_entries = _collect_journal_entries(tenant, content_type=ct, object_id=invoice.pk)
    subledger_items = _collect_subledger_items(tenant, content_type=ct, object_id=invoice.pk)
    je_ids = [je.pk for je in journal_entries]
    lines_by_je = _lines_for_journal_entries(je_ids)
    allocations = list(
        SubledgerAllocation.all_objects.filter(
            tenant=tenant,
            subledger_item_id__in=[item.pk for item in subledger_items],
        ).select_related('journal_entry', 'subledger_item')
    )
    bank_matches = list(
        BankTransaction.all_objects.filter(
            tenant=tenant,
            matched_journal_entry_id__in=je_ids,
        ).order_by('transaction_date', 'pk')
    )
    partner = invoice.company_to
    return DocumentPostingContext(
        tenant_slug=tenant.slug,
        document_type='invoice',
        document_id=invoice.pk,
        document_number=invoice.invoice_number or f'invoice-{invoice.pk}',
        document_status=invoice.status,
        partner_id=partner.pk if partner else None,
        partner_name=partner.name if partner else None,
        journal_entries=journal_entries,
        subledger_items=subledger_items,
        allocations=allocations,
        bank_matches=bank_matches,
        lines_by_je=lines_by_je,
    )


def collect_document_posting_context(tenant, document) -> DocumentPostingContext:
    if isinstance(document, Expense):
        return collect_expense_posting_context(tenant, document)
    if isinstance(document, Invoice):
        return collect_invoice_posting_context(tenant, invoice)
    raise TypeError(f'Unsupported document type: {type(document)!r}')


def _je_has_partner_subledger_lines(
    je_id: int,
    lines_by_je: dict[int, list[JournalEntryLine]],
    *,
    payable: bool,
    receivable: bool,
) -> bool:
    bases = set()
    if payable:
        bases |= PARTNER_PAYABLE_BASES
    if receivable:
        bases |= PARTNER_RECEIVABLE_BASES
    for line in lines_by_je.get(je_id, []):
        if _account_base(line.account.account_code) in bases:
            if line.debit_amount or line.credit_amount:
                return True
    return False


def _je_is_gl_bypass_ap_candidate(
    je: JournalEntry,
    lines_by_je: dict[int, list[JournalEntryLine]],
) -> bool:
    """Posted JE credits bank/cash with expense/asset debit and no 2201 line."""
    if je.status != 'posted':
        return False
    if extract_document_type(je.description or '') in ('expense_approved', 'expense_paid'):
        return False
    lines = lines_by_je.get(je.pk, [])
    if not lines:
        return False
    has_cash_credit = False
    has_expense_or_asset_debit = False
    has_2201 = False
    for line in lines:
        base = _account_base(line.account.account_code)
        if base in PARTNER_PAYABLE_BASES and (line.debit_amount or line.credit_amount):
            has_2201 = True
        if base in CASH_BASES and (line.credit_amount or Decimal('0')) > Decimal('0'):
            has_cash_credit = True
        if base not in PARTNER_PAYABLE_BASES | CASH_BASES and (line.debit_amount or Decimal('0')) > Decimal('0'):
            has_expense_or_asset_debit = True
    return has_cash_credit and has_expense_or_asset_debit and not has_2201


def _has_standard_obligation_posted(ctx: DocumentPostingContext) -> bool:
    if ctx.document_type == 'expense':
        marker = 'expense_approved'
        payable = True
        receivable = False
    else:
        marker = 'invoice_issued'
        payable = False
        receivable = True
    for je in ctx.posted_journal_entries:
        if extract_document_type(je.description or '') != marker:
            continue
        if _je_has_partner_subledger_lines(je.pk, ctx.lines_by_je, payable=payable, receivable=receivable):
            return True
    return False


def _has_active_subledger_with_live_obligation(ctx: DocumentPostingContext) -> bool:
    for item in ctx.canonical_active_subledger_items:
        je = item.journal_entry
        if je is None or je.status != 'posted':
            continue
        return True
    return False


def classify_document_mismatch(ctx: DocumentPostingContext) -> list[str]:
    """Return all matching pattern codes. P8 only when list would otherwise be empty."""
    patterns: list[str] = []
    active_items = ctx.active_subledger_items
    canonical_active = ctx.canonical_active_subledger_items
    cancelled_items = [item for item in ctx.subledger_items if item.status == 'cancelled']

    if ctx.is_paid_like and not canonical_active and ctx.subledger_items:
        patterns.append(P1_CANCELLED_SUBLEDGER_PAID_DOC)
    if ctx.is_paid_like and not canonical_active and not ctx.subledger_items and ctx.posted_journal_entries:
        patterns.append(P1_CANCELLED_SUBLEDGER_PAID_DOC)

    for je in ctx.posted_journal_entries:
        marker = extract_document_type(je.description or '')
        if marker is not None:
            continue
        payable = ctx.document_type == 'expense'
        receivable = ctx.document_type == 'invoice'
        if not _je_has_partner_subledger_lines(
            je.pk, ctx.lines_by_je, payable=payable, receivable=receivable
        ):
            patterns.append(P2_MANUAL_JE_NO_SUBLEDGER)
            break

    if ctx.document_type == 'expense' and ctx.partner_id:
        for je in ctx.posted_journal_entries:
            if _je_is_gl_bypass_ap_candidate(je, ctx.lines_by_je):
                patterns.append(P3_GL_BYPASS_AP_CANDIDATE)
                break

    for item in active_items:
        je = item.journal_entry
        if je is not None and je.status == 'reversed':
            patterns.append(P4_SUBLEDGER_REVERSED_OBLIGATION_JE)
            break

    if ctx.partner_id:
        for item in ctx.subledger_items:
            if item.partner_id != ctx.partner_id:
                patterns.append(P5_ORPHAN_SUBLEDGER_WRONG_PARTNER)
                break

    if ctx.bank_matches and canonical_active:
        allocated_je_ids = {alloc.journal_entry_id for alloc in ctx.allocations}
        for tx in ctx.bank_matches:
            if tx.matched_journal_entry_id and tx.matched_journal_entry_id not in allocated_je_ids:
                patterns.append(P6_BANK_SETTLED_NO_ALLOCATION)
                break
    elif ctx.bank_matches and not canonical_active:
        patterns.append(P6_BANK_SETTLED_NO_ALLOCATION)

    if len(canonical_active) > 1:
        patterns.append(P7_DUPLICATE_ACTIVE_SUBLEDGER)

    if not patterns and ctx.is_posted_like:
        if _has_standard_obligation_posted(ctx) and _has_active_subledger_with_live_obligation(ctx):
            if ctx.canonical_active_subledger_items:
                patterns.append(P8_STANDARD_PATH_OK)

    return patterns


def _orphan_subledger_items(ctx: DocumentPostingContext) -> list[dict[str, Any]]:
    if not ctx.partner_id:
        return []
    return [
        _subledger_item_dict(item)
        for item in ctx.subledger_items
        if item.partner_id != ctx.partner_id
    ]


def build_document_audit_record(ctx: DocumentPostingContext) -> dict[str, Any]:
    patterns = classify_document_mismatch(ctx)
    je_summaries = {je.pk: _je_summary(je) for je in ctx.journal_entries}
    _annotate_je_accounts(je_summaries, ctx.lines_by_je)

    active_jes = [je_summaries[je.pk] for je in ctx.journal_entries if je.status == 'posted']
    severities = sorted({PATTERN_SEVERITY.get(code, 'unknown') for code in patterns})

    return {
        'tenant': ctx.tenant_slug,
        'patterns': patterns,
        'severity': severities[0] if len(severities) == 1 else severities,
        'document_type': ctx.document_type,
        'document_id': ctx.document_id,
        'document_number': ctx.document_number,
        'partner_id': ctx.partner_id,
        'partner_name': ctx.partner_name,
        'document_status': ctx.document_status,
        'subledger_items': [_subledger_item_dict(item) for item in ctx.subledger_items],
        'orphan_subledger_items': _orphan_subledger_items(ctx),
        'active_journal_entries': active_jes,
        'bank_matches': [
            {
                'btx_id': tx.pk,
                'je_id': tx.matched_journal_entry_id,
                'transaction_date': tx.transaction_date.isoformat() if tx.transaction_date else None,
                'amount': _money_str(tx.amount),
                'match_status': tx.match_status,
                'description': (tx.description or '')[:120],
            }
            for tx in ctx.bank_matches
        ],
        'allocations': [
            {
                'id': alloc.pk,
                'amount': _money_str(alloc.amount),
                'journal_entry_id': alloc.journal_entry_id,
                'subledger_item_id': alloc.subledger_item_id,
            }
            for alloc in ctx.allocations
        ],
    }


def audit_tenant_document_mismatches(tenant) -> dict[str, Any]:
    """Scan expenses and invoices; return full inventory (read-only)."""
    records: list[dict[str, Any]] = []
    pattern_counts: dict[str, int] = {}

    expenses = Expense.all_objects.filter(
        tenant=tenant,
        status__in=EXPENSE_SCAN_STATUSES,
    ).select_related('supplier').order_by('expense_date', 'pk')
    expense_list = list(expenses)

    invoices = Invoice.all_objects.filter(
        tenant=tenant,
        status__in=INVOICE_SCAN_STATUSES,
    ).select_related('company_to').order_by('issue_date', 'pk')
    invoice_list = list(invoices)

    for expense in expense_list:
        ctx = collect_expense_posting_context(tenant, expense)
        record = build_document_audit_record(ctx)
        if record['patterns']:
            records.append(record)
            for code in record['patterns']:
                pattern_counts[code] = pattern_counts.get(code, 0) + 1

    for invoice in invoice_list:
        ctx = collect_invoice_posting_context(tenant, invoice)
        record = build_document_audit_record(ctx)
        if record['patterns']:
            records.append(record)
            for code in record['patterns']:
                pattern_counts[code] = pattern_counts.get(code, 0) + 1

    p8_only = [r for r in records if r['patterns'] == [P8_STANDARD_PATH_OK]]
    mismatch_records = [r for r in records if r['patterns'] != [P8_STANDARD_PATH_OK]]

    return {
        'tenant': tenant.slug,
        'summary': {
            'documents_scanned': len(expense_list) + len(invoice_list),
            'documents_with_patterns': len(records),
            'documents_with_mismatches': len(mismatch_records),
            'documents_p8_only': len(p8_only),
            'pattern_counts': dict(sorted(pattern_counts.items())),
        },
        'records': sorted(
            mismatch_records,
            key=lambda row: (
                row['patterns'][0] if row['patterns'] else '',
                row['document_type'],
                row['document_id'],
            ),
        ),
        'p8_records': sorted(p8_only, key=lambda row: (row['document_type'], row['document_id'])),
    }
