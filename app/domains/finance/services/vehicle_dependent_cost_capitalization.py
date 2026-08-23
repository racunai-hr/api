"""Expense-scoped capitalization of vehicle dependent costs (4120 → 0373)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import (
    AssetJournalLinkRole,
    FixedAsset,
    FixedAssetJournalLink,
    FixedAssetStatus,
    JournalEntry,
    SubledgerAllocation,
    SubledgerItem,
    VATLedgerEntry,
    VATPeriod,
    VATProjectionRun,
)
from accounting.services.posting import post_document
from accounts.models import AuditLog
from domains.assets.services.acquisition_cost import reconcile_acquisition_cost
from domains.assets.services.journal_links import apply_asset_journal_links
from domains.finance.services.subledger import allocate_payment, reverse_allocation
from expenses.models import Expense, ExpensePostingProfile


APPROVED_MARKER = '[expense_approved]'


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal('0.01'))


def _money_str(value) -> str:
    return f'{_money(value):.2f}'


def _account_base(code: str | None) -> str:
    if not code:
        return ''
    return code.split('-', 1)[0]


@dataclass(frozen=True)
class Prerequisite:
    key: str
    expected: Any
    actual: Any
    ok: bool
    detail: str = ''


@dataclass
class CapitalizationPlan:
    case_id: str
    tenant: str
    asset_id: int
    dry_run: bool = True
    writes_allowed: bool = False
    prerequisites_ok: bool = False
    prerequisites: list[Prerequisite] = field(default_factory=list)
    before: dict[str, Any] = field(default_factory=dict)
    planned_after: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    expenses: list[dict[str, Any]] = field(default_factory=list)
    vat_july: dict[str, Any] | None = None
    blockers: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            'case_id': self.case_id,
            'tenant': self.tenant,
            'asset_id': self.asset_id,
            'expected_from_amount': self.before.get('acquisition_cost'),
            'expenses': [
                {
                    'expense_id': row.get('expense_id'),
                    'expense_number': row.get('expense_number'),
                    'original_je_id': row.get('original_je_id'),
                    'item_id': row.get('item_id'),
                    'allocation_id': row.get('allocation_id'),
                    'payment_je_id': row.get('payment_je_id'),
                    'amount': row.get('amount'),
                    'tax_amount': row.get('tax_amount'),
                    'net_amount': row.get('net_amount'),
                    'already_applied': row.get('already_applied', False),
                }
                for row in self.expenses
            ],
            'vat_july': self.vat_july,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            'case_id': self.case_id,
            'tenant': self.tenant,
            'asset_id': self.asset_id,
            'dry_run': self.dry_run,
            'writes_allowed': self.writes_allowed,
            'prerequisites_ok': self.prerequisites_ok,
            'blockers': self.blockers,
            'prerequisites': [asdict(item) for item in self.prerequisites],
            'before': self.before,
            'planned_after': self.planned_after,
            'steps': self.steps,
            'expenses': self.expenses,
            'vat_july': self.vat_july,
            'forbidden': self.forbidden,
            'out_of_scope': self.out_of_scope,
            'snapshot': self.snapshot(),
        }


def _check(key: str, expected: Any, actual: Any, *, detail: str = '') -> Prerequisite:
    return Prerequisite(
        key=key,
        expected=expected,
        actual=actual,
        ok=expected == actual,
        detail=detail,
    )


def _expense_ct():
    return ContentType.objects.get_for_model(Expense)


def _approved_entries(tenant, expense: Expense):
    return list(
        JournalEntry.all_objects.filter(
            tenant=tenant,
            source_content_type=_expense_ct(),
            source_object_id=expense.pk,
            description__startswith=APPROVED_MARKER,
        ).order_by('pk')
    )


def _debit_totals(entry: JournalEntry) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for line in entry.lines.select_related('account'):
        if line.debit_amount <= 0:
            continue
        base = _account_base(line.account.account_code)
        totals[base] = totals.get(base, Decimal('0.00')) + line.debit_amount
    return totals


def snapshot_vat_july(tenant) -> dict[str, Any] | None:
    period = VATPeriod.all_objects.filter(tenant=tenant, year=2026, month=7).first()
    if period is None:
        return None
    run = (
        VATProjectionRun.all_objects.filter(tenant=tenant, vat_period=period)
        .order_by('-pk')
        .first()
    )
    ledger_rows = list(
        VATLedgerEntry.all_objects.filter(vat_period=period)
        .order_by('pk')
        .values_list(
            'pk',
            'ledger_type',
            'document_number',
            'base_amount',
            'vat_amount',
            'origin',
        )
    )
    return {
        'period_id': period.pk,
        'status': period.status,
        'run_id': run.pk if run else None,
        'input_fingerprint': run.input_fingerprint if run else '',
        'output_fingerprint': run.output_fingerprint if run else '',
        'ledger_count': len(ledger_rows),
        'ledger_rows': [
            {
                'pk': pk,
                'ledger_type': ledger_type,
                'document_number': document_number,
                'base_amount': _money_str(base),
                'vat_amount': _money_str(vat),
                'origin': origin,
            }
            for pk, ledger_type, document_number, base, vat, origin in ledger_rows
        ],
    }


def _inspect_expense(tenant, expense_id: int) -> dict[str, Any]:
    expense = Expense.all_objects.filter(tenant=tenant, pk=expense_id).first()
    if expense is None:
        return {
            'expense_id': expense_id,
            'found': False,
            'already_applied': False,
            'blockers': [f'Expense {expense_id} nije pronađen.'],
        }

    entries = _approved_entries(tenant, expense)
    posted = [entry for entry in entries if entry.status == 'posted']
    reversed_entries = [entry for entry in entries if entry.status == 'reversed']
    original = posted[0] if posted else (reversed_entries[0] if reversed_entries else None)

    items = list(
        SubledgerItem.all_objects.filter(
            tenant=tenant,
            source_content_type=_expense_ct(),
            source_object_id=expense.pk,
        ).order_by('pk')
    )
    live_items = [item for item in items if item.status != 'cancelled']
    live_item = live_items[0] if live_items else None
    allocation = None
    if live_item is not None:
        allocation = (
            SubledgerAllocation.all_objects.filter(subledger_item=live_item)
            .select_related('journal_entry')
            .order_by('pk')
            .first()
        )

    already = (
        expense.posting_profile == ExpensePostingProfile.ASSET_PURCHASE
        and bool(reversed_entries)
        and bool(posted)
        and '0373' in _debit_totals(posted[-1])
    )

    net = _money(expense.amount) - _money(expense.tax_amount)
    blockers: list[str] = []
    if expense.status != 'paid':
        blockers.append(f'{expense.expense_number} status={expense.status!r} (want paid)')
    if not already and expense.posting_profile != ExpensePostingProfile.OPEX:
        blockers.append(
            f'{expense.expense_number} posting_profile={expense.posting_profile!r} (want opex)'
        )
    if original is None:
        blockers.append(f'{expense.expense_number} nema expense_approved JE')
    elif not already:
        if original.status != 'posted':
            blockers.append(f'{expense.expense_number} JE {original.pk} status={original.status}')
        if original.matched_bank_transactions.exists():
            blockers.append(f'{expense.expense_number} JE {original.pk} ima bank match')
        debits = _debit_totals(original)
        if _money(debits.get('4120')) != net:
            blockers.append(
                f'{expense.expense_number} D 4120={_money_str(debits.get("4120"))} '
                f'(want {_money_str(net)})'
            )
        if _money(expense.tax_amount) > 0 and _money(debits.get('1400')) != _money(expense.tax_amount):
            blockers.append(f'{expense.expense_number} pretporez na originalu nije {_money_str(expense.tax_amount)}')
    if live_item is None:
        blockers.append(f'{expense.expense_number} nema aktivnu AP stavku')
    elif not already and live_item.status != 'closed':
        blockers.append(f'{expense.expense_number} AP {live_item.pk} status={live_item.status}')
    if allocation is None:
        blockers.append(f'{expense.expense_number} nema alokaciju plaćanja')

    return {
        'expense_id': expense.pk,
        'expense_number': expense.expense_number,
        'found': True,
        'already_applied': already,
        'status': expense.status,
        'posting_profile': expense.posting_profile,
        'category_id': expense.category_id,
        'amount': _money_str(expense.amount),
        'tax_amount': _money_str(expense.tax_amount),
        'net_amount': _money_str(net),
        'original_je_id': original.pk if original else None,
        'original_je_number': original.entry_number if original else None,
        'item_id': live_item.pk if live_item else None,
        'allocation_id': allocation.pk if allocation else None,
        'payment_je_id': allocation.journal_entry_id if allocation else None,
        'blockers': blockers,
    }


def plan_vehicle_dependent_cost_capitalization(
    tenant,
    *,
    asset_id: int,
    expense_ids: list[int],
    case_id: str = 'VEHICLE-DEP-COST',
) -> CapitalizationPlan:
    plan = CapitalizationPlan(
        case_id=case_id,
        tenant=tenant.slug,
        asset_id=asset_id,
        forbidden=[
            'Ne reaktivirati cancelled AP stavke.',
            'Ne knjižiti ručnu temeljnicu umjesto post_document.',
            'Ne zadavati acquisition_cost ručno.',
            'Ne dirati PDV razdoblje 2026-07.',
            'Ne dirati osiguranje (T-2026-0017).',
        ],
        out_of_scope=[
            'Classification cleanup kategorija',
            'Aktivacija / amortizacija',
            'AVR za police osiguranja',
        ],
    )
    if not expense_ids:
        plan.blockers.append('expense_ids je prazan.')
        return plan

    asset = FixedAsset.all_objects.filter(tenant=tenant, pk=asset_id).first()
    plan.prerequisites.append(
        _check('asset_exists', True, asset is not None, detail=f'asset_id={asset_id}')
    )
    if asset is None:
        plan.blockers.append(f'FixedAsset {asset_id} nije pronađen.')
        return plan

    plan.prerequisites.append(
        _check('asset_status', FixedAssetStatus.IN_PREPARATION, asset.status)
    )
    construction = asset.construction_account.account_code if asset.construction_account_id else ''
    plan.prerequisites.append(_check('construction_account', '0373', construction))

    expense_rows = [_inspect_expense(tenant, expense_id) for expense_id in expense_ids]
    plan.expenses = expense_rows
    for row in expense_rows:
        plan.blockers.extend(row['blockers'])

    from domains.assets.read.service import list_asset_journal_entries

    recon = list_asset_journal_entries(tenant, asset.pk)['reconciliation']
    plan.before = {
        'acquisition_cost': _money_str(asset.acquisition_cost),
        'capitalized_net': recon['capitalized_net'],
        'status': asset.status,
    }
    extra_net = sum(
        (_money(row['net_amount']) for row in expense_rows if row.get('found') and not row['already_applied']),
        Decimal('0.00'),
    )
    planned_cost = _money(asset.acquisition_cost) + extra_net
    plan.planned_after = {
        'acquisition_cost': 'derived_from_capitalized_net',
        'expected_capitalized_net_if_gl_matches': _money_str(planned_cost),
        'expense_profiles': 'asset_purchase (auditirana reklasifikacija)',
        'original_jes': 'reversed',
        'correction_jes': 'posted D 0373 + D 1400 / K 2201',
        'links': 'dependent_cost na correction JE',
    }
    plan.steps = [
        {
            'seq': 1,
            'writes': True,
            'action': 'reclassify_posting_profile',
            'target': 'Expense.posting_profile',
            'detail': 'Trajna auditirana reklasifikacija opex → asset_purchase (nije tehnički pripremni korak).',
        },
        {
            'seq': 2,
            'writes': True,
            'action': 'reverse_allocation',
            'target': 'SubledgerAllocation',
            'detail': 'Maknuti postojeću AP alokaciju s plaćanja; cancelled stavke se ne reaktiviraju.',
        },
        {
            'seq': 3,
            'writes': True,
            'action': 'reverse_original_je',
            'target': 'JournalEntry',
            'detail': 'Storno postojećeg expense_approved (4120/1400/2201).',
        },
        {
            'seq': 4,
            'writes': True,
            'action': 'post_document',
            'target': 'expense_approved',
            'detail': 'Correction JE D 0373 + D 1400 / K 2201 + nova AP stavka.',
        },
        {
            'seq': 5,
            'writes': True,
            'action': 'allocate_payment',
            'target': 'postojeća payment JE',
            'detail': 'Ista PFC/payment temeljnica zatvara novu AP stavku.',
        },
        {
            'seq': 6,
            'writes': True,
            'action': 'link_dependent_cost',
            'target': 'FixedAssetJournalLink',
            'detail': 'Samo nova correction JE; stornirani 4120 par se ne veže.',
        },
        {
            'seq': 7,
            'writes': True,
            'action': 'reconcile_acquisition_cost',
            'target': 'FixedAsset',
            'detail': 'Cilj se izvodi iz capitalized_net, ne iz operatorovog iznosa.',
        },
    ]
    plan.vat_july = snapshot_vat_july(tenant)
    plan.prerequisites_ok = not plan.blockers and all(item.ok for item in plan.prerequisites)
    if not plan.prerequisites_ok:
        plan.writes_allowed = False
    return plan


def _assert_snapshot_matches(plan: CapitalizationPlan, snapshot: dict[str, Any]) -> None:
    if int(snapshot.get('asset_id') or 0) != plan.asset_id:
        raise ValidationError({'snapshot': 'asset_id u snapshotu se ne poklapa.'})
    expected = snapshot.get('expenses') or []
    actual = {row['expense_id']: row for row in plan.expenses}
    if [row['expense_id'] for row in expected] != [row['expense_id'] for row in plan.expenses]:
        raise ValidationError({'snapshot': 'Popis expense_id se ne poklapa s planom.'})
    for row in expected:
        current = actual[row['expense_id']]
        for key in (
            'original_je_id',
            'item_id',
            'allocation_id',
            'payment_je_id',
            'amount',
            'tax_amount',
            'net_amount',
        ):
            if current.get(key) != row.get(key) and not current.get('already_applied'):
                raise ValidationError(
                    {'snapshot': f'Expense {row["expense_id"]} {key} drift: {current.get(key)!r} != {row.get(key)!r}.'},
                )


def _reclassify_expense(expense: Expense, *, user, reason: str, case_id: str) -> None:
    if expense.posting_profile == ExpensePostingProfile.ASSET_PURCHASE:
        return
    previous = expense.posting_profile
    expense.posting_profile = ExpensePostingProfile.ASSET_PURCHASE
    expense._remediation_posting_profile_migration = {
        'from_profile': ExpensePostingProfile.OPEX,
        'to_profile': ExpensePostingProfile.ASSET_PURCHASE,
        'reason': reason,
        'case_id': case_id,
    }
    expense._skip_auto_posting = True
    expense.save(update_fields=['posting_profile'])
    AuditLog.all_objects.create(
        tenant=expense.tenant,
        user=user,
        action='expense_posting_profile_reclassified',
        model_name='Expense',
        object_id=str(expense.pk),
        changes={
            'case_id': case_id,
            'reason': reason,
            'from_profile': previous,
            'to_profile': ExpensePostingProfile.ASSET_PURCHASE,
        },
    )


def _apply_one_expense(tenant, row: dict[str, Any], *, user, reason: str, case_id: str, asset_id: int) -> dict[str, Any]:
    expense = Expense.all_objects.select_for_update().get(tenant=tenant, pk=row['expense_id'])
    if row['already_applied']:
        posted = [entry for entry in _approved_entries(tenant, expense) if entry.status == 'posted']
        return {
            'expense_id': expense.pk,
            'skipped': True,
            'correction_je_id': posted[-1].pk if posted else None,
        }

    _reclassify_expense(expense, user=user, reason=reason, case_id=case_id)

    allocation = SubledgerAllocation.all_objects.select_related('journal_entry', 'subledger_item').get(
        pk=row['allocation_id'],
        tenant=tenant,
    )
    payment_je = allocation.journal_entry
    reverse_allocation(allocation)

    original = JournalEntry.all_objects.select_for_update().get(pk=row['original_je_id'], tenant=tenant)
    reversal = original.reverse(user)

    correction = post_document(
        tenant,
        expense,
        'expense_approved',
        user,
        entry_date=timezone.now().date(),
    )
    if correction is None:
        raise ValidationError({'post_document': f'{expense.expense_number}: correction JE nije nastala.'})
    debits = _debit_totals(correction)
    if _money(debits.get('0373')) != _money(row['net_amount']):
        raise ValidationError(
            {
                'post_document': (
                    f'{expense.expense_number}: D 0373={_money_str(debits.get("0373"))} '
                    f'want {_money(row["net_amount"])}.'
                ),
            },
        )
    if _money(row['tax_amount']) > 0 and _money(debits.get('1400')) != _money(row['tax_amount']):
        raise ValidationError(
            {'post_document': f'{expense.expense_number}: pretporez nije prenesen na correction JE.'},
        )
    if '4120' in debits:
        raise ValidationError({'post_document': f'{expense.expense_number}: correction JE i dalje knjiži 4120.'})

    new_alloc = allocate_payment(tenant, source=expense, journal_entry=payment_je)
    if new_alloc is None:
        raise ValidationError({'allocate_payment': f'{expense.expense_number}: alokacija plaćanja nije uspjela.'})

    apply_asset_journal_links(
        tenant,
        asset_id,
        entries=[(correction.pk, AssetJournalLinkRole.DEPENDENT_COST)],
        case_id=case_id,
        reason=reason,
        user=user,
    )

    latest = (
        AuditLog.all_objects.filter(
            tenant=tenant,
            action='expense_posting_profile_reclassified',
            object_id=str(expense.pk),
        )
        .order_by('-pk')
        .first()
    )
    if latest is not None:
        changes = dict(latest.changes or {})
        if changes.get('case_id') == case_id:
            changes.update(
                {
                    'original_je_id': original.pk,
                    'reversal_je_id': reversal.pk,
                    'correction_je_id': correction.pk,
                    'payment_je_id': payment_je.pk,
                },
            )
            latest.changes = changes
            latest.save(update_fields=['changes'])

    return {
        'expense_id': expense.pk,
        'skipped': False,
        'original_je_id': original.pk,
        'reversal_je_id': reversal.pk,
        'correction_je_id': correction.pk,
        'allocation_id': new_alloc.pk,
    }


@transaction.atomic
def execute_vehicle_dependent_cost_capitalization(
    tenant,
    *,
    asset_id: int,
    expense_ids: list[int],
    snapshot: dict[str, Any],
    user,
    reason: str,
    case_id: str,
) -> dict[str, Any]:
    reason = (reason or '').strip()
    case_id = (case_id or '').strip()
    if not reason:
        raise ValidationError({'reason': 'Razlog je obavezan.'})
    if not case_id:
        raise ValidationError({'case_id': 'case_id je obavezan.'})
    if not snapshot:
        raise ValidationError({'snapshot': 'Odobreni snapshot je obavezan.'})

    vat_before = snapshot_vat_july(tenant)
    plan = plan_vehicle_dependent_cost_capitalization(
        tenant,
        asset_id=asset_id,
        expense_ids=expense_ids,
        case_id=case_id,
    )
    if not plan.prerequisites_ok:
        raise ValidationError({'plan': plan.blockers or ['Prerequisites nisu zadovoljeni.']})
    _assert_snapshot_matches(plan, snapshot)

    applied = []
    for row in plan.expenses:
        applied.append(
            _apply_one_expense(
                tenant,
                row,
                user=user,
                reason=reason,
                case_id=case_id,
                asset_id=asset_id,
            )
        )

    asset = FixedAsset.all_objects.get(tenant=tenant, pk=asset_id)
    reconcile_acquisition_cost(
        asset,
        expected_from_amount=plan.before['acquisition_cost'],
        user=user,
        reason=reason,
        case_id=case_id,
    )

    vat_after = snapshot_vat_july(tenant)
    if vat_before != vat_after:
        raise ValidationError({'vat_july': 'PDV 2026-07 se promijenio tijekom remediationa.'})

    verify = verify_vehicle_dependent_cost_capitalization(
        tenant,
        asset_id=asset_id,
        expense_ids=expense_ids,
        vat_before=vat_before,
        case_id=case_id,
    )
    if not verify['ok']:
        raise ValidationError({'verify': verify['failures']})

    return {
        'asset_id': asset_id,
        'applied': applied,
        'verify': verify,
    }


def verify_vehicle_dependent_cost_capitalization(
    tenant,
    *,
    asset_id: int,
    expense_ids: list[int],
    vat_before: dict[str, Any] | None = None,
    case_id: str | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    from domains.assets.read.service import list_asset_journal_entries
    from domains.assets.services.journal_links import verify_asset_journal_links

    asset = FixedAsset.all_objects.filter(tenant=tenant, pk=asset_id).first()
    if asset is None:
        return {'ok': False, 'failures': [f'FixedAsset {asset_id} missing']}

    payload = list_asset_journal_entries(tenant, asset_id)
    recon = payload['reconciliation']
    if not recon['balanced']:
        failures.append(
            f"reconciliation unbalanced capitalized_net={recon['capitalized_net']} "
            f"acquisition_cost={recon['acquisition_cost']}"
        )

    links = verify_asset_journal_links(tenant, asset_id)
    if not links['ok']:
        failures.append('verify_asset_journal_links failed')

    for expense_id in expense_ids:
        expense = Expense.all_objects.filter(tenant=tenant, pk=expense_id).first()
        if expense is None:
            failures.append(f'expense {expense_id} missing')
            continue
        if expense.posting_profile != ExpensePostingProfile.ASSET_PURCHASE:
            failures.append(f'{expense.expense_number} profile={expense.posting_profile}')
        posted = [entry for entry in _approved_entries(tenant, expense) if entry.status == 'posted']
        reversed_entries = [entry for entry in _approved_entries(tenant, expense) if entry.status == 'reversed']
        if not reversed_entries:
            failures.append(f'{expense.expense_number} nema stornirani original')
        if not posted:
            failures.append(f'{expense.expense_number} nema posted correction JE')
            continue
        correction = posted[-1]
        debits = _debit_totals(correction)
        net = _money(expense.amount) - _money(expense.tax_amount)
        if _money(debits.get('0373')) != net:
            failures.append(f'{expense.expense_number} correction D 0373 != {_money_str(net)}')
        if _money(expense.tax_amount) > 0 and _money(debits.get('1400')) != _money(expense.tax_amount):
            failures.append(f'{expense.expense_number} correction pretporez missing')
        if '4120' in debits:
            failures.append(f'{expense.expense_number} correction još ima 4120')
        if not FixedAssetJournalLink.all_objects.filter(
            tenant=tenant,
            fixed_asset_id=asset_id,
            journal_entry=correction,
            role=AssetJournalLinkRole.DEPENDENT_COST,
        ).exists():
            failures.append(f'{expense.expense_number} nije vezan kao dependent_cost')
        live = (
            SubledgerItem.all_objects.filter(
                tenant=tenant,
                source_content_type=_expense_ct(),
                source_object_id=expense.pk,
            )
            .exclude(status='cancelled')
        )
        if live.count() != 1 or live.first().status != 'closed':
            failures.append(f'{expense.expense_number} AP nije točno jedna zatvorena stavka')
        cancelled = SubledgerItem.all_objects.filter(
            tenant=tenant,
            source_content_type=_expense_ct(),
            source_object_id=expense.pk,
            status='cancelled',
        )
        for item in cancelled:
            if SubledgerAllocation.all_objects.filter(subledger_item=item).exists():
                failures.append(f'{expense.expense_number} cancelled AP {item.pk} još ima alokaciju')

    vat_after = snapshot_vat_july(tenant)
    if vat_before is not None and vat_before != vat_after:
        failures.append('PDV 2026-07 snapshot se razlikuje')

    if case_id:
        if not AuditLog.all_objects.filter(
            tenant=tenant,
            action='fixed_asset_acquisition_cost_reconciled',
            object_id=str(asset_id),
            changes__case_id=case_id,
        ).exists():
            if recon['balanced'] and recon['capitalized_net'] == recon['acquisition_cost']:
                pass
            else:
                failures.append('nedostaje AuditLog reconcile')

    return {
        'ok': not failures,
        'failures': failures,
        'reconciliation': recon,
        'vat_july': vat_after,
    }
