"""T-2026-0009 asset_purchase remediation — dry-run + fail-closed write orchestrator.

Korak 2c dry-run never writes. Write path (``execute=True``) is fail-closed:
re-check fingerprint before first mutation, stop on any exception, verify final
invariants. Normal ``Expense.save`` still freezes ``posting_profile``; only the
narrow remediation migrate helper may change opex → asset_purchase with audit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import (
    FixedAsset,
    JournalEntry,
    JournalEntryLine,
    PostingRule,
    SubledgerAllocation,
    SubledgerItem,
)
from accounting.services.posting import post_document
from accounts.models import AuditLog
from banking.models import BankTransaction
from banking.reconciliation import match_transaction_to_journal_entry, unmatch_transaction
from domains.assets.services.retarget import retarget_purchase_journal_entry
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
LOCKED_ASSET_PURCHASE_RULE_ID = 106
LOCKED_JE57_LINES = (
    ('14022', '2000.00', '0.00'),
    ('24022', '0.00', '2000.00'),
)

# Accidental migrate-signal posting (2026-08-23) — must stay reversed forever.
LOCKED_INCIDENT_JE178_ID = 178  # expense_approved D0373/P2201, reversed
LOCKED_INCIDENT_JE179_ID = 179  # expense_paid D2201/P1000, reversed
LOCKED_INCIDENT_JE180_ID = 180  # storno of 179
LOCKED_INCIDENT_JE181_ID = 181  # storno of 178
LOCKED_INCIDENT_ITEM61_ID = 61  # cancelled; created from JE178; no allocations
LOCKED_INCIDENT_AUDIT_PROFILE_FLIP_ID = 308  # posting_profile opex → asset_purchase


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


def _expense_subledger_item_ids(tenant, expense_id: int, *, exclude_cancelled: bool = False) -> list[int]:
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(Expense)
    qs = SubledgerItem.all_objects.filter(
        tenant=tenant,
        source_content_type=ct,
        source_object_id=expense_id,
    )
    if exclude_cancelled:
        qs = qs.exclude(status='cancelled')
    return list(qs.order_by('pk').values_list('pk', flat=True))


def _je_debit_credit_bases(je: JournalEntry | None) -> tuple[set[str], set[str]]:
    if je is None:
        return set(), set()
    lines = _je_lines_summary(je)
    debit = {
        _account_base(row['account_code'])
        for row in lines
        if Decimal(row['debit']) > 0
    }
    credit = {
        _account_base(row['account_code'])
        for row in lines
        if Decimal(row['credit']) > 0
    }
    return debit, credit


def _incident_fingerprint_checks(tenant, expense: Expense) -> list[LockedPrerequisite]:
    """Locked state of the 2026-08-23 accidental posting incident (JE178–181 / item61)."""
    je178 = JournalEntry.all_objects.filter(tenant=tenant, pk=LOCKED_INCIDENT_JE178_ID).first()
    je179 = JournalEntry.all_objects.filter(tenant=tenant, pk=LOCKED_INCIDENT_JE179_ID).first()
    je180 = JournalEntry.all_objects.filter(tenant=tenant, pk=LOCKED_INCIDENT_JE180_ID).first()
    je181 = JournalEntry.all_objects.filter(tenant=tenant, pk=LOCKED_INCIDENT_JE181_ID).first()
    item61 = SubledgerItem.all_objects.filter(tenant=tenant, pk=LOCKED_INCIDENT_ITEM61_ID).first()
    item61_alloc_count = SubledgerAllocation.all_objects.filter(
        tenant=tenant,
        subledger_item_id=LOCKED_INCIDENT_ITEM61_ID,
    ).count()
    active_item_ids = _expense_subledger_item_ids(tenant, expense.pk, exclude_cancelled=True)
    all_item_ids = _expense_subledger_item_ids(tenant, expense.pk, exclude_cancelled=False)
    bank_on_incident = list(
        BankTransaction.all_objects.filter(
            tenant=tenant,
            matched_journal_entry_id__in=[
                LOCKED_INCIDENT_JE178_ID,
                LOCKED_INCIDENT_JE179_ID,
                LOCKED_INCIDENT_JE180_ID,
                LOCKED_INCIDENT_JE181_ID,
            ],
        ).values_list('pk', flat=True)
    )
    fa_on_incident = list(
        FixedAsset.all_objects.filter(
            tenant=tenant,
            purchase_journal_entry_id__in=[
                LOCKED_INCIDENT_JE178_ID,
                LOCKED_INCIDENT_JE179_ID,
                LOCKED_INCIDENT_JE180_ID,
                LOCKED_INCIDENT_JE181_ID,
            ],
        ).values_list('pk', flat=True)
    )
    d178, c178 = _je_debit_credit_bases(je178)
    d179, c179 = _je_debit_credit_bases(je179)
    d180, c180 = _je_debit_credit_bases(je180)
    d181, c181 = _je_debit_credit_bases(je181)

    profile_flip = AuditLog.all_objects.filter(
        tenant=tenant,
        pk=LOCKED_INCIDENT_AUDIT_PROFILE_FLIP_ID,
    ).first()
    profile_flip_ok = bool(
        profile_flip
        and profile_flip.model_name.endswith('expense')
        and str(profile_flip.object_id) == '16'
        and (profile_flip.changes or {}).get('posting_profile', {}).get('new') == 'asset_purchase'
        and (profile_flip.changes or {}).get('posting_profile', {}).get('old') == 'opex'
    )
    audit_je178_create = AuditLog.all_objects.filter(
        tenant=tenant,
        action='create',
        model_name__icontains='journalentry',
        object_id=str(LOCKED_INCIDENT_JE178_ID),
    ).exists()
    audit_je179_create = AuditLog.all_objects.filter(
        tenant=tenant,
        action='create',
        model_name__icontains='journalentry',
        object_id=str(LOCKED_INCIDENT_JE179_ID),
    ).exists()
    audit_je180_create = AuditLog.all_objects.filter(
        tenant=tenant,
        action='create',
        model_name__icontains='journalentry',
        object_id=str(LOCKED_INCIDENT_JE180_ID),
    ).exists()
    audit_je181_create = AuditLog.all_objects.filter(
        tenant=tenant,
        action='create',
        model_name__icontains='journalentry',
        object_id=str(LOCKED_INCIDENT_JE181_ID),
    ).exists()

    return [
        _check(
            'expense.posting_profile',
            ExpensePostingProfile.OPEX,
            expense.posting_profile,
            detail='Must be restored to opex after accidental migrate-signal incident',
        ),
        _check('incident.je178.exists', True, je178 is not None),
        _check('incident.je178.status', 'reversed', getattr(je178, 'status', None)),
        _check('incident.je178.debit_bases', {'0373'}, d178),
        _check('incident.je178.credit_bases', {'2201'}, c178),
        _check('incident.je179.exists', True, je179 is not None),
        _check('incident.je179.status', 'reversed', getattr(je179, 'status', None)),
        _check('incident.je179.debit_bases', {'2201'}, d179),
        _check('incident.je179.credit_bases', {'1000'}, c179),
        _check('incident.je180.exists', True, je180 is not None),
        _check('incident.je180.status', 'posted', getattr(je180, 'status', None)),
        _check(
            'incident.je180.reversed_entry_id',
            LOCKED_INCIDENT_JE179_ID,
            getattr(je180, 'reversed_entry_id', None),
            detail='JE180 must be the storno of JE179',
        ),
        _check('incident.je180.debit_bases', {'1000'}, d180),
        _check('incident.je180.credit_bases', {'2201'}, c180),
        _check('incident.je181.exists', True, je181 is not None),
        _check('incident.je181.status', 'posted', getattr(je181, 'status', None)),
        _check(
            'incident.je181.reversed_entry_id',
            LOCKED_INCIDENT_JE178_ID,
            getattr(je181, 'reversed_entry_id', None),
            detail='JE181 must be the storno of JE178',
        ),
        _check('incident.je181.debit_bases', {'2201'}, d181),
        _check('incident.je181.credit_bases', {'0373'}, c181),
        _check('incident.item61.exists', True, item61 is not None),
        _check('incident.item61.status', 'cancelled', getattr(item61, 'status', None)),
        _check(
            'incident.item61.journal_entry_id',
            LOCKED_INCIDENT_JE178_ID,
            getattr(item61, 'journal_entry_id', None),
        ),
        _check('incident.item61.partner_id', 17, getattr(item61, 'partner_id', None)),
        _check('incident.item61.allocation_count', 0, item61_alloc_count),
        _check(
            'incident.expense16.active_subledger_item_ids',
            [],
            active_item_ids,
            detail='No open/partial/closed active SubledgerItem for Expense16; only cancelled allowed',
        ),
        _check(
            'incident.expense16.subledger_item_ids',
            [28, LOCKED_INCIDENT_ITEM61_ID],
            all_item_ids,
            detail='Exactly cancelled item28 + cancelled incident item61',
        ),
        _check(
            'incident.bank_match_on_je178_181',
            [],
            bank_on_incident,
            detail='Incident JEs must not hold any bank match',
        ),
        _check(
            'incident.fixed_asset_on_je178_181',
            [],
            fa_on_incident,
            detail='No FixedAsset may point at incident JEs',
        ),
        _check(
            'incident.audit.profile_flip_308',
            True,
            profile_flip_ok,
            detail='AuditLog 308 must record accidental opex→asset_purchase flip on Expense16',
        ),
        _check('incident.audit.je178_create', True, audit_je178_create),
        _check('incident.audit.je179_create', True, audit_je179_create),
        _check('incident.audit.je180_create', True, audit_je180_create),
        _check('incident.audit.je181_create', True, audit_je181_create),
    ]



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
    je57 = JournalEntry.all_objects.filter(tenant=tenant, pk=57).first()

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

    je57_lines = tuple(
        (_account_base(row['account_code']), row['debit'], row['credit'])
        for row in (_je_lines_summary(je57) if je57 else [])
    )

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
        _check(
            'posting_rule.asset_purchase_id',
            LOCKED_ASSET_PURCHASE_RULE_ID,
            getattr(
                PostingRule.all_objects.filter(
                    tenant=tenant,
                    pk=LOCKED_ASSET_PURCHASE_RULE_ID,
                    is_active=True,
                ).first(),
                'pk',
                None,
            ),
            detail='Fingerprint locks seeded rule id 106 on finestar',
        ),
        _check('je57.exists', True, je57 is not None),
        _check('je57.status', 'posted', getattr(je57, 'status', None)),
        _check(
            'je57.lines',
            LOCKED_JE57_LINES,
            je57_lines,
            detail='JE57 must stay byte/logically untouched',
        ),
    ] + _incident_fingerprint_checks(tenant, expense)
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
        'incident_2026_08_23': {
            'note': (
                'Accidental Expense.save auto-post while testing profile migrate; '
                'reversed same day. Locked in pre-write fingerprint.'
            ),
            'je178_status': _je_status(tenant, LOCKED_INCIDENT_JE178_ID),
            'je179_status': _je_status(tenant, LOCKED_INCIDENT_JE179_ID),
            'je180_status': _je_status(tenant, LOCKED_INCIDENT_JE180_ID),
            'je181_status': _je_status(tenant, LOCKED_INCIDENT_JE181_ID),
            'item61_status': (
                SubledgerItem.all_objects.filter(
                    tenant=tenant, pk=LOCKED_INCIDENT_ITEM61_ID,
                ).values_list('status', flat=True).first()
            ),
            'audit_profile_flip_id': LOCKED_INCIDENT_AUDIT_PROFILE_FLIP_ID,
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
            'detail': 'migrate_expense_posting_profile_opex_to_asset_purchase (narrow escape + AuditLog)',
            'writes': True,
            'service': 'migrate_expense_posting_profile_opex_to_asset_purchase',
        },
        {
            'seq': 2,
            'action': 'unmatch_bank',
            'target': 'BTX 18',
            'detail': 'unmatch_transaction — detach from JE 49',
            'writes': True,
            'service': 'unmatch_transaction',
        },
        {
            'seq': 3,
            'action': 'reverse_bypass_je',
            'target': 'JE 49',
            'detail': 'JournalEntry.reverse — storno D0373/K1000 bypass; item28 stays cancelled',
            'writes': True,
            'service': 'JournalEntry.reverse',
        },
        {
            'seq': 4,
            'action': 'post_canonical_obligation',
            'target': 'post_document(expense_approved)',
            'detail': 'post_document → D0373/P2201 + new SubledgerItem via Finance hook',
            'writes': True,
            'service': 'post_document',
        },
        {
            'seq': 5,
            'action': 'post_canonical_payment',
            'target': 'post_document(expense_paid)',
            'detail': 'post_document → D2201/P1000 + SubledgerAllocation via Finance hook',
            'writes': True,
            'service': 'post_document',
        },
        {
            'seq': 6,
            'action': 'rematch_bank',
            'target': 'BTX 18',
            'detail': 'match_transaction_to_journal_entry → new payment JE (never JE49)',
            'writes': True,
            'service': 'match_transaction_to_journal_entry',
        },
        {
            'seq': 7,
            'action': 'retarget_fixed_asset',
            'target': 'FixedAsset 2',
            'detail': 'retarget_purchase_journal_entry → new obligation JE + AuditLog',
            'writes': True,
            'service': 'retarget_purchase_journal_entry',
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


@dataclass
class RemediationExecutionResult:
    case_id: str
    tenant: str
    executed: bool
    dry_run: bool
    prerequisites_ok: bool
    steps_completed: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    invariant_failures: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RemediationAborted(ValidationError):
    """Fingerprint drift or mid-run failure — no partial remediation."""


def _assert_gl_bases(
    je: JournalEntry,
    *,
    debit_base: str,
    credit_base: str,
    amount: str,
) -> None:
    lines = _je_lines_summary(je)
    debit_bases = {
        _account_base(row['account_code'])
        for row in lines
        if Decimal(row['debit']) > 0
    }
    credit_bases = {
        _account_base(row['account_code'])
        for row in lines
        if Decimal(row['credit']) > 0
    }
    amounts = {
        row['debit'] if Decimal(row['debit']) > 0 else row['credit']
        for row in lines
        if Decimal(row['debit']) > 0 or Decimal(row['credit']) > 0
    }
    if debit_bases != {debit_base} or credit_bases != {credit_base}:
        raise RemediationAborted(
            f'JE {je.pk} GL bases {sorted(debit_bases)}/{sorted(credit_bases)} '
            f'!= [{debit_base}]/[{credit_base}]'
        )
    if amount not in amounts:
        raise RemediationAborted(
            f'JE {je.pk} missing amount {amount}; got {sorted(amounts)}'
        )


def migrate_expense_posting_profile_opex_to_asset_purchase(
    expense: Expense,
    *,
    user,
    reason: str,
    case_id: str,
) -> Expense:
    """Narrow remediation escape: opex → asset_purchase + AuditLog.

    Normal ``Expense.save`` still rejects profile changes after draft.
    """
    reason = (reason or '').strip()
    case_id = (case_id or '').strip()
    if not reason:
        raise ValidationError({'reason': 'Razlog remediation migracije je obavezan.'})
    if not case_id:
        raise ValidationError({'case_id': 'case_id je obavezan.'})
    if expense.posting_profile != ExpensePostingProfile.OPEX:
        raise ValidationError({
            'posting_profile': f'Očekivan opex, aktualno {expense.posting_profile!r}.',
        })

    previous = expense.posting_profile
    expense.posting_profile = ExpensePostingProfile.ASSET_PURCHASE
    expense._remediation_posting_profile_migration = {
        'from_profile': ExpensePostingProfile.OPEX,
        'to_profile': ExpensePostingProfile.ASSET_PURCHASE,
        'reason': reason,
        'case_id': case_id,
    }
    # Avoid accounting.signals auto post_document on paid Expense.save —
    # remediation posts obligation/payment explicitly after unmatch/reverse.
    expense._skip_auto_posting = True
    expense.save(update_fields=['posting_profile'])

    AuditLog.all_objects.create(
        tenant=expense.tenant,
        user=user,
        action='expense_posting_profile_remediation',
        model_name='Expense',
        object_id=str(expense.pk),
        changes={
            'case_id': case_id,
            'reason': reason,
            'from_profile': previous,
            'to_profile': ExpensePostingProfile.ASSET_PURCHASE,
        },
    )
    return expense


def verify_t20260009_invariants(
    tenant,
    *,
    obligation_je: JournalEntry,
    payment_je: JournalEntry,
    reversal_je: JournalEntry | None,
) -> list[str]:
    """Return invariant failures (empty = pass)."""
    failures: list[str] = []

    expense = Expense.all_objects.filter(tenant=tenant, pk=16).first()
    if expense is None:
        return ['expense 16 missing']
    if expense.posting_profile != ExpensePostingProfile.ASSET_PURCHASE:
        failures.append(f'expense.posting_profile={expense.posting_profile!r}')
    if expense.status != 'paid':
        failures.append(f'expense.status={expense.status!r}')

    je49 = JournalEntry.all_objects.filter(tenant=tenant, pk=49).first()
    if je49 is None or je49.status != 'reversed':
        failures.append(f'je49.status={getattr(je49, "status", None)!r} (want reversed)')
    if reversal_je is None or reversal_je.reversed_entry_id != 49:
        failures.append(
            'reversal JE missing or '
            f'reversed_entry_id={getattr(reversal_je, "reversed_entry_id", None)!r}'
        )

    item28 = SubledgerItem.all_objects.filter(tenant=tenant, pk=28).first()
    if item28 is None or item28.status != 'cancelled':
        failures.append(f'item28.status={getattr(item28, "status", None)!r}')

    try:
        _assert_gl_bases(obligation_je, debit_base='0373', credit_base='2201', amount='8000.00')
    except RemediationAborted as exc:
        failures.extend(exc.messages if hasattr(exc, 'messages') else [str(exc)])

    try:
        _assert_gl_bases(payment_je, debit_base='2201', credit_base='1000', amount='8000.00')
    except RemediationAborted as exc:
        failures.extend(exc.messages if hasattr(exc, 'messages') else [str(exc)])

    item61 = SubledgerItem.all_objects.filter(tenant=tenant, pk=LOCKED_INCIDENT_ITEM61_ID).first()
    if item61 is None or item61.status != 'cancelled':
        failures.append(f'item61.status={getattr(item61, "status", None)!r}')

    new_items = list(
        SubledgerItem.all_objects.filter(
            tenant=tenant,
            source_object_id=16,
            partner_id=17,
            journal_entry=obligation_je,
        )
    )
    if len(new_items) != 1:
        failures.append(
            f'expected exactly 1 new SubledgerItem for expense 16; got {len(new_items)}'
        )
    else:
        item = new_items[0]
        if item.status != 'closed':
            failures.append(f'new item status={item.status!r} (want closed)')
        if _money(item.original_amount) != '8000.00':
            failures.append(f'new item amount={item.original_amount!r}')
        if item.journal_entry_id != obligation_je.pk:
            failures.append('new item not linked to obligation JE')
        alloc = SubledgerAllocation.all_objects.filter(
            tenant=tenant,
            subledger_item=item,
            journal_entry=payment_je,
        ).first()
        if alloc is None:
            failures.append('SubledgerAllocation on payment JE missing')
        elif _money(alloc.amount) != '8000.00':
            failures.append(f'allocation amount={alloc.amount!r}')

    btx18 = BankTransaction.all_objects.filter(tenant=tenant, pk=18).first()
    if btx18 is None or btx18.match_status != 'matched':
        failures.append(f'btx18.match_status={getattr(btx18, "match_status", None)!r}')
    elif btx18.matched_journal_entry_id != payment_je.pk:
        failures.append(
            f'btx18 matched to {btx18.matched_journal_entry_id}, want payment {payment_je.pk}'
        )

    fa2 = FixedAsset.all_objects.filter(tenant=tenant, pk=2).first()
    if fa2 is None or fa2.purchase_journal_entry_id != obligation_je.pk:
        failures.append(
            f'fa2.purchase_journal_entry_id='
            f'{getattr(fa2, "purchase_journal_entry_id", None)!r}'
        )

    je57 = JournalEntry.all_objects.filter(tenant=tenant, pk=57).first()
    if je57 is None or je57.status != 'posted':
        failures.append(f'je57.status={getattr(je57, "status", None)!r}')
    else:
        actual_lines = tuple(
            (_account_base(row['account_code']), row['debit'], row['credit'])
            for row in _je_lines_summary(je57)
        )
        if actual_lines != LOCKED_JE57_LINES:
            failures.append(f'je57.lines changed: {actual_lines!r}')

    ctx = collect_expense_posting_context(tenant, expense)
    leftover = set(classify_document_mismatch(ctx)) & EXPECTED_PATTERNS
    if leftover:
        failures.append(f'inventory still reports {sorted(leftover)} for expense 16')

    return failures


def execute_t20260009_asset_purchase_remediation(
    tenant,
    user,
    *,
    execute: bool = False,
    reason: str = '',
    confirm_case_id: str = '',
    expense_id: int = 16,
) -> RemediationExecutionResult:
    """Orchestrate remediation. Default ``execute=False`` is dry-run only (0 writes)."""
    plan = plan_t20260009_asset_purchase_remediation(tenant, expense_id=expense_id)
    result = RemediationExecutionResult(
        case_id=CASE_ID,
        tenant=tenant.slug,
        executed=False,
        dry_run=not execute,
        prerequisites_ok=plan.prerequisites_ok,
        blockers=list(plan.blockers),
    )

    if not plan.prerequisites_ok:
        return result

    if not execute:
        result.result = {'plan': plan.to_dict()}
        return result

    if confirm_case_id != CASE_ID:
        raise RemediationAborted(
            f'confirm_case_id must be {CASE_ID!r} to authorize writes.'
        )
    reason = (reason or '').strip()
    if len(reason) < 10:
        raise RemediationAborted('reason must be at least 10 characters.')

    completed: list[dict[str, Any]] = []

    try:
        with transaction.atomic():
            gate = plan_t20260009_asset_purchase_remediation(tenant, expense_id=expense_id)
            if not gate.prerequisites_ok:
                raise RemediationAborted(
                    'Fingerprint drifted since dry-run: ' + '; '.join(gate.blockers)
                )

            expense = Expense.all_objects.select_for_update().get(
                tenant=tenant, pk=expense_id,
            )
            btx18 = BankTransaction.all_objects.select_for_update().get(
                tenant=tenant, pk=18,
            )
            je49 = JournalEntry.all_objects.select_for_update().get(
                tenant=tenant, pk=49,
            )
            fa2 = FixedAsset.all_objects.select_for_update().get(
                tenant=tenant, pk=2,
            )

            migrate_expense_posting_profile_opex_to_asset_purchase(
                expense,
                user=user,
                reason=reason,
                case_id=CASE_ID,
            )
            completed.append({'seq': 1, 'action': 'set_posting_profile', 'ok': True})

            unmatch_transaction(btx18, user)
            completed.append({'seq': 2, 'action': 'unmatch_bank', 'ok': True})

            reversal_je = je49.reverse(user)
            completed.append({
                'seq': 3,
                'action': 'reverse_bypass_je',
                'ok': True,
                'reversal_je_id': reversal_je.pk,
            })

            expense.refresh_from_db()
            obligation_je = post_document(tenant, expense, 'expense_approved', user)
            if obligation_je is None:
                raise RemediationAborted('post_document(expense_approved) returned None')
            _assert_gl_bases(
                obligation_je, debit_base='0373', credit_base='2201', amount='8000.00',
            )
            completed.append({
                'seq': 4,
                'action': 'post_canonical_obligation',
                'ok': True,
                'obligation_je_id': obligation_je.pk,
            })

            payment_je = post_document(tenant, expense, 'expense_paid', user)
            if payment_je is None:
                raise RemediationAborted('post_document(expense_paid) returned None')
            _assert_gl_bases(
                payment_je, debit_base='2201', credit_base='1000', amount='8000.00',
            )
            completed.append({
                'seq': 5,
                'action': 'post_canonical_payment',
                'ok': True,
                'payment_je_id': payment_je.pk,
            })

            btx18.refresh_from_db()
            match_transaction_to_journal_entry(btx18, payment_je, user)
            completed.append({'seq': 6, 'action': 'rematch_bank', 'ok': True})

            retarget_purchase_journal_entry(
                fa2,
                from_journal_entry=je49,
                to_journal_entry=obligation_je,
                user=user,
                reason=reason,
                case_id=CASE_ID,
            )
            completed.append({'seq': 7, 'action': 'retarget_fixed_asset', 'ok': True})

            failures = verify_t20260009_invariants(
                tenant,
                obligation_je=obligation_je,
                payment_je=payment_je,
                reversal_je=reversal_je,
            )
            if failures:
                raise RemediationAborted('Invariant failures: ' + '; '.join(failures))
            completed.append({'seq': 8, 'action': 'verify', 'ok': True})

            result.executed = True
            result.dry_run = False
            result.steps_completed = completed
            result.invariant_failures = []
            result.result = {
                'obligation_je_id': obligation_je.pk,
                'payment_je_id': payment_je.pk,
                'reversal_je_id': reversal_je.pk,
                'reason': reason,
            }
    except Exception:
        result.steps_completed = completed
        result.executed = False
        raise

    return result

def verify_t20260009_incident_gate(tenant, *, expense_id: int = 16) -> RemediationDryRunPlan:
    """Read-only incident + remediation fingerprint gate. Never writes.

    Same prerequisites as ``plan_t20260009_asset_purchase_remediation`` (includes
    JE178–181 / item61 locks). Convenience alias for GO review.
    """
    return plan_t20260009_asset_purchase_remediation(tenant, expense_id=expense_id)
