"""Read-only dry-run remediation plan for expense asset_purchase bypass cases.

Korak 2c — no DB writes. Fail-closed when locked prerequisites diverge from the
inventory snapshot used to approve remediation design (T-2026-0009 / expense 16).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from accounting.models import FixedAsset, JournalEntry, JournalEntryLine, PostingRule, SubledgerItem
from banking.models import BankTransaction
from domains.finance.services.document_integrity import (
    P1_CANCELLED_SUBLEDGER_PAID_DOC,
    P2_MANUAL_JE_NO_SUBLEDGER,
    P3_GL_BYPASS_AP_CANDIDATE,
    P6_BANK_SETTLED_NO_ALLOCATION,
    classify_document_mismatch,
    collect_expense_posting_context,
)
from expenses.models import Expense, ExpensePostingProfile

CASE_ID = 'T-2026-0009'
EXPECTED_PATTERNS = frozenset({
    P1_CANCELLED_SUBLEDGER_PAID_DOC,
    P2_MANUAL_JE_NO_SUBLEDGER,
    P3_GL_BYPASS_AP_CANDIDATE,
    P6_BANK_SETTLED_NO_ALLOCATION,
})


@dataclass(frozen=True)
class LockedPrerequisite:
    key: str
    expected: Any
    actual: Any
    ok: bool
    detail: str = ''


@dataclass
class RemediationDryRunPlan:
    case_id: str
    tenant: str
    dry_run: bool = True
    writes_allowed: bool = False
    prerequisites_ok: bool = False
    prerequisites: list[LockedPrerequisite] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    before: dict[str, Any] = field(default_factory=dict)
    planned_after: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'case_id': self.case_id,
            'tenant': self.tenant,
            'dry_run': self.dry_run,
            'writes_allowed': self.writes_allowed,
            'prerequisites_ok': self.prerequisites_ok,
            'blockers': self.blockers,
            'patterns': self.patterns,
            'prerequisites': [asdict(item) for item in self.prerequisites],
            'before': self.before,
            'planned_after': self.planned_after,
            'steps': self.steps,
            'forbidden': self.forbidden,
            'out_of_scope': self.out_of_scope,
        }


def _money(value: Decimal | None) -> str:
    if value is None:
        return '0.00'
    return f'{Decimal(value):.2f}'


def _account_base(code: str | None) -> str:
    if not code:
        return ''
    return code.split('-', 1)[0]


def _je_lines_summary(je: JournalEntry) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in (
        JournalEntryLine.objects.filter(journal_entry=je)
        .select_related('account')
        .order_by('pk')
    ):
        rows.append({
            'account_code': line.account.account_code,
            'debit': _money(line.debit_amount),
            'credit': _money(line.credit_amount),
        })
    return rows


def _je_status(tenant, je_id: int) -> str | None:
    je = JournalEntry.all_objects.filter(tenant=tenant, pk=je_id).only('status').first()
    return je.status if je else None


def _optional_je_snapshot(tenant, je_id: int, *, role: str) -> dict[str, Any] | None:
    je = JournalEntry.all_objects.filter(tenant=tenant, pk=je_id).first()
    if je is None:
        return None
    return {
        'id': je.pk,
        'entry_number': je.entry_number,
        'status': je.status,
        'lines': _je_lines_summary(je),
        'role': role,
    }


def _has_asset_purchase_obligation_rule(tenant) -> bool:
    rules = PostingRule.all_objects.filter(
        tenant=tenant,
        document_type='expense_approved',
        is_active=True,
        debit_account_code='0373',
        credit_account_code='2201',
    )
    for rule in rules:
        profiles = (rule.condition or {}).get('posting_profile') or []
        if isinstance(profiles, str):
            profiles = [profiles]
        if ExpensePostingProfile.ASSET_PURCHASE in profiles:
            return True
    return False


def _check(key: str, expected: Any, actual: Any, *, detail: str = '') -> LockedPrerequisite:
    return LockedPrerequisite(
        key=key,
        expected=expected,
        actual=actual,
        ok=expected == actual,
        detail=detail,
    )


def plan_t20260009_asset_purchase_remediation(
    tenant,
    *,
    expense_id: int = 16,
) -> RemediationDryRunPlan:
    """Fail-closed dry-run plan for finestar expense T-2026-0009 (id=16). Never writes."""
    plan = RemediationDryRunPlan(
        case_id=CASE_ID,
        tenant=tenant.slug,
        forbidden=[
            'Reactivate SubledgerItem 28',
            'Create SubledgerAllocation against JE 49',
            'Cosmetic kartica/UI fix without GL',
            'Repost as OPEX 4120',
            'Touch JE 57 (PDV reverse charge) in this AP remediation',
        ],
        out_of_scope=[
            'JE 57 reverse-charge stays posted as-is',
            'FixedAsset activation / depreciation',
            'PFC / SubledgerItem 39 (different source document)',
        ],
    )

    try:
        expense = Expense.all_objects.select_related(
            'category',
            'category__default_account',
            'supplier',
        ).get(tenant=tenant, pk=expense_id)
    except Expense.DoesNotExist:
        plan.blockers.append(f'Expense id={expense_id} not found for tenant {tenant.slug}')
        plan.prerequisites_ok = False
        plan.steps = [{
            'seq': 0,
            'action': 'blocked',
            'target': 'remediation',
            'detail': 'Fail-closed: expense not found',
            'writes': False,
            'blockers': list(plan.blockers),
        }]
        return plan

    ctx = collect_expense_posting_context(tenant, expense)
    patterns = classify_document_mismatch(ctx)
    plan.patterns = list(patterns)

    je49 = JournalEntry.all_objects.filter(tenant=tenant, pk=49).first()
    item28 = (
        SubledgerItem.all_objects.select_related('partner', 'journal_entry')
        .filter(tenant=tenant, pk=28)
        .first()
    )
    btx18 = BankTransaction.all_objects.filter(tenant=tenant, pk=18).first()
    fa2 = (
        FixedAsset.all_objects.select_related('purchase_journal_entry')
        .filter(tenant=tenant, pk=2)
        .first()
    )

    je49_lines = _je_lines_summary(je49) if je49 else []
    je49_debit_bases = {
        _account_base(row['account_code'])
        for row in je49_lines
        if Decimal(row['debit']) > 0
    }
    je49_credit_bases = {
        _account_base(row['account_code'])
        for row in je49_lines
        if Decimal(row['credit']) > 0
    }

    pattern_hit = sorted(set(patterns) & EXPECTED_PATTERNS)
    checks = [
        _check('expense.id', 16, expense.pk),
        _check('expense.expense_number', 'T-2026-0009', expense.expense_number),
        _check('expense.status', 'paid', expense.status),
        _check('expense.amount', '8000.00', _money(expense.amount)),
        _check('expense.tax_amount', '0.00', _money(expense.tax_amount)),
        _check('expense.supplier_id', 17, expense.supplier_id),
        _check(
            'expense.settlement_method',
            'business_account',
            expense.settlement_method or '',
        ),
        _check(
            'patterns.required_set',
            sorted(EXPECTED_PATTERNS),
            pattern_hit,
            detail='Must still include P1+P2+P3+P6',
        ),
        _check('je49.exists', True, je49 is not None),
        _check('je49.status', 'posted', getattr(je49, 'status', None)),
        _check('je49.source_object_id', 16, getattr(je49, 'source_object_id', None)),
        _check('je49.debit_bases', {'0373'}, je49_debit_bases),
        _check('je49.credit_bases', {'1000'}, je49_credit_bases),
        _check('item28.exists', True, item28 is not None),
        _check('item28.status', 'cancelled', getattr(item28, 'status', None)),
        _check('item28.partner_id', 17, getattr(item28, 'partner_id', None)),
        _check(
            'item28.original_amount',
            '8000.00',
            _money(getattr(item28, 'original_amount', None)),
        ),
        _check('item28.source_object_id', 16, getattr(item28, 'source_object_id', None)),
        _check('btx18.exists', True, btx18 is not None),
        _check('btx18.match_status', 'matched', getattr(btx18, 'match_status', None)),
        _check(
            'btx18.matched_journal_entry_id',
            49,
            getattr(btx18, 'matched_journal_entry_id', None),
        ),
        _check('btx18.amount', '8000.00', _money(getattr(btx18, 'amount', None))),
        _check('fixed_asset2.exists', True, fa2 is not None),
        _check('fixed_asset2.status', 'in_preparation', getattr(fa2, 'status', None)),
        _check(
            'fixed_asset2.purchase_journal_entry_id',
            49,
            getattr(fa2, 'purchase_journal_entry_id', None),
        ),
        _check(
            'posting_rule.asset_purchase_obligation',
            True,
            _has_asset_purchase_obligation_rule(tenant),
            detail=(
                'Active expense_approved rule D0373/C2201 with '
                'condition.posting_profile including asset_purchase '
                '(seed via ensure_default_posting_rules — separate GO)'
            ),
        ),
    ]
    plan.prerequisites = checks
    plan.prerequisites_ok = all(item.ok for item in checks)
    plan.blockers = [
        f'{item.key}: expected={item.expected!r} actual={item.actual!r}'
        + (f' ({item.detail})' if item.detail else '')
        for item in checks
        if not item.ok
    ]

    category_account = None
    if expense.category_id and expense.category.default_account_id:
        category_account = expense.category.default_account.account_code

    plan.before = {
        'expense': {
            'id': expense.pk,
            'expense_number': expense.expense_number,
            'status': expense.status,
            'amount': _money(expense.amount),
            'tax_amount': _money(expense.tax_amount),
            'posting_profile': expense.posting_profile,
            'settlement_method': expense.settlement_method,
            'category': expense.category.name if expense.category_id else None,
            'category_default_account': category_account,
            'supplier_id': expense.supplier_id,
            'supplier_name': expense.supplier.name if expense.supplier_id else None,
        },
        'je49': None if je49 is None else {
            'id': je49.pk,
            'entry_number': je49.entry_number,
            'status': je49.status,
            'lines': je49_lines,
            'role': 'active_bypass_D0373_K1000',
        },
        'je57': _optional_je_snapshot(tenant, 57, role='pdv_reverse_charge_out_of_scope'),
        'item28': None if item28 is None else {
            'id': item28.pk,
            'status': item28.status,
            'partner_id': item28.partner_id,
            'original_amount': _money(item28.original_amount),
            'open_amount': _money(item28.open_amount),
            'journal_entry_id': item28.journal_entry_id,
            'journal_entry_status': (
                item28.journal_entry.status if item28.journal_entry_id else None
            ),
        },
        'btx18': None if btx18 is None else {
            'id': btx18.pk,
            'amount': _money(btx18.amount),
            'match_status': btx18.match_status,
            'matched_journal_entry_id': btx18.matched_journal_entry_id,
            'transaction_date': (
                btx18.transaction_date.isoformat() if btx18.transaction_date else None
            ),
        },
        'fixed_asset2': None if fa2 is None else {
            'id': fa2.pk,
            'name': fa2.name,
            'status': fa2.status,
            'acquisition_cost': _money(fa2.acquisition_cost),
            'purchase_journal_entry_id': fa2.purchase_journal_entry_id,
        },
        'already_reversed_opex_path': {
            'je99_status': _je_status(tenant, 99),
            'je100_status': _je_status(tenant, 100),
            'storno_je108_status': _je_status(tenant, 108),
            'storno_je109_status': _je_status(tenant, 109),
            'note': 'Wrong OPEX 4120 path already reversed; do not rebuild it',
        },
    }

    plan.planned_after = {
        'expense': {
            'posting_profile': ExpensePostingProfile.ASSET_PURCHASE,
            'status': 'paid',
            'settlement_method': 'business_account',
            'amount': '8000.00',
        },
        'je49': {
            'id': 49,
            'status': 'reversed_or_stornoed',
            'note': 'Active bypass removed; no allocations may point here',
        },
        'new_obligation_je': {
            'document_type': 'expense_approved',
            'gl': 'D 0373 / P 2201',
            'amount': '8000.00',
            'creates': 'new SubledgerItem payable open for partner 17',
        },
        'new_payment_je': {
            'document_type': 'expense_paid',
            'gl': 'D 2201 / P 1000',
            'amount': '8000.00',
            'creates': 'SubledgerAllocation closing the new SubledgerItem',
        },
        'item28': {
            'id': 28,
            'status': 'cancelled',
            'note': 'Remains cancelled forever; not reused',
        },
        'btx18': {
            'id': 18,
            'match_status': 'matched',
            'matched_journal_entry_id': 'NEW_payment_je_id',
            'note': 'Unmatch from JE49 then rematch to canonical payment JE',
        },
        'fixed_asset2': {
            'id': 2,
            'status': 'in_preparation',
            'purchase_journal_entry_id': 'NEW_obligation_je_id',
            'note': 'Points at canonical D0373/P2201 obligation JE, not JE49',
        },
        'je57': {
            'id': 57,
            'status': 'posted_unchanged',
        },
    }

    if not plan.prerequisites_ok:
        plan.steps = [{
            'seq': 0,
            'action': 'blocked',
            'target': 'remediation',
            'detail': 'Fail-closed: resolve blockers before any write/storno/repost GO',
            'writes': False,
            'blockers': plan.blockers,
        }]
        return plan

    plan.steps = [
        {
            'seq': 1,
            'action': 'set_posting_profile',
            'target': 'Expense 16',
            'detail': 'posting_profile opex → asset_purchase (explicit; not inferred from 0373)',
            'writes': True,
        },
        {
            'seq': 2,
            'action': 'unmatch_bank',
            'target': 'BTX 18',
            'detail': 'Detach matched_journal_entry from JE 49',
            'writes': True,
        },
        {
            'seq': 3,
            'action': 'reverse_bypass_je',
            'target': 'JE 49',
            'detail': 'Storno/reverse active D0373/K1000 bypass; leave item28 cancelled',
            'writes': True,
        },
        {
            'seq': 4,
            'action': 'post_canonical_obligation',
            'target': 'post_document(expense_approved)',
            'detail': 'Via asset_purchase profile → D0373/P2201 + new SubledgerItem',
            'writes': True,
        },
        {
            'seq': 5,
            'action': 'post_canonical_payment',
            'target': 'post_document(expense_paid) or bank open-item reconcile',
            'detail': 'D2201/P1000 + SubledgerAllocation on the new item',
            'writes': True,
        },
        {
            'seq': 6,
            'action': 'rematch_bank',
            'target': 'BTX 18',
            'detail': 'Match to new payment JE (never back to JE49)',
            'writes': True,
        },
        {
            'seq': 7,
            'action': 'retarget_fixed_asset',
            'target': 'FixedAsset 2',
            'detail': 'purchase_journal_entry → new obligation JE',
            'writes': True,
        },
        {
            'seq': 8,
            'action': 'verify',
            'target': 'inventory + partner 17 statement + FA2',
            'detail': 'Expense 16 no longer P1+P2+P3+P6; item28 still cancelled; JE57 untouched',
            'writes': False,
        },
    ]
    return plan
