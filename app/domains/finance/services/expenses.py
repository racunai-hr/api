"""Approve OCR/manual expense drafts and post AP (expense_approved)."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.http import Http404

from accounting.services.posting import build_document_posting_plan, post_document
from domains.finance.services.account_resolver import (
    load_postable_account,
    load_tenant_category,
)
from domains.finance.services.cost_centers import cost_center_ref
from domains.finance.services.cost_center_resolver import load_bookable_cost_center
from expenses.models import Expense, ExpenseAccountSource, SettlementMethod

# Sentinel: omit ``settlement_method`` → keep legacy fill-in (blank → business_account).
_USE_EXISTING_SETTLEMENT_DEFAULT = object()
_OMIT = object()


class ExpenseApproveBadRequest(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ExpenseApproveConflict(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _money(value) -> str:
    return f'{Decimal(value):.2f}'


def _account_ref(account) -> dict | None:
    if account is None:
        return None
    return {
        'id': account.pk,
        'code': account.account_code,
        'name': account.account_name,
        'active': account.is_active,
    }


def expense_dto(expense: Expense) -> dict:
    return {
        'id': expense.pk,
        'expense_number': expense.expense_number,
        'status': expense.status,
        'amount': f'{expense.amount:.2f}',
        'currency': expense.currency,
        'expense_date': expense.expense_date.isoformat() if expense.expense_date else None,
        'due_date': expense.due_date.isoformat() if expense.due_date else None,
        'supplier_id': expense.supplier_id,
        'category_id': expense.category_id,
        'expense_account_id': expense.expense_account_id,
        'expense_account_source': expense.expense_account_source,
        'cost_center_id': expense.cost_center_id,
        'settlement_method': expense.settlement_method or '',
        'approved_by_id': expense.approved_by_id,
    }


def serialize_document_posting_plan(expense: Expense, plan) -> dict:
    """Map ``build_document_posting_plan`` (+ expense identity) to a preview DTO.

    Does not resolve accounts. ``plan`` is the only source of GL lines.
    """
    category = getattr(expense, 'category', None)
    resolved_account = plan.expense_account if plan is not None else expense.expense_account
    lines = []
    if plan is not None:
        for line in plan.lines:
            lines.append({
                'amount_field': line.amount_field,
                'description': line.description,
                'amount': _money(line.amount),
                'debit': _account_ref(line.debit_account),
                'credit': _account_ref(line.credit_account),
                'debit_cost_center': cost_center_ref(line.debit_cost_center),
                'credit_cost_center': cost_center_ref(line.credit_cost_center),
            })
    return {
        'category': (
            {'id': category.pk, 'name': category.name} if category is not None else None
        ),
        'expense_account': _account_ref(resolved_account),
        'account_source': plan.account_source if plan is not None else expense.expense_account_source,
        'warnings': list(plan.warnings) if plan is not None else [],
        'can_approve': (
            expense.status == 'draft'
            and expense.supplier_id is not None
            and plan is not None
        ),
        'lines': lines,
    }


def posting_preview(*, tenant, expense_id: int) -> dict:
    expense = (
        Expense.all_objects.filter(tenant=tenant, pk=expense_id)
        .select_related('category', 'category__default_account', 'expense_account', 'supplier')
        .first()
    )
    if expense is None:
        raise Http404()
    plan = build_document_posting_plan(tenant, expense, 'expense_approved')
    return serialize_document_posting_plan(expense, plan)


@transaction.atomic
def update_draft_expense_posting(
    *,
    tenant,
    expense_id: int,
    category_id=_OMIT,
    expense_account_id=_OMIT,
    cost_center_id=_OMIT,
) -> dict:
    expense = (
        Expense.all_objects.select_for_update(of=('self',))
        .filter(tenant=tenant, pk=expense_id)
        .select_related('category', 'expense_account')
        .first()
    )
    if expense is None:
        raise Http404()
    if expense.status != 'draft':
        raise ExpenseApproveConflict(
            'not_draft',
            'Vrsta troška i konto mogu se mijenjati samo dok je nalog u nacrtu.',
        )
    if category_id is _OMIT and expense_account_id is _OMIT and cost_center_id is _OMIT:
        raise ExpenseApproveBadRequest(
            'empty_patch',
            'Potrebna je category_id, expense_account_id ili cost_center_id.',
        )

    if category_id is not _OMIT:
        expense.category = load_tenant_category(tenant, category_id)
        if expense.expense_account_id is None:
            expense.expense_account_source = ExpenseAccountSource.CATEGORY_DEFAULT

    if expense_account_id is not _OMIT:
        if expense_account_id is None:
            expense.expense_account = None
            expense.expense_account_source = ExpenseAccountSource.CATEGORY_DEFAULT
        else:
            expense.expense_account = load_postable_account(tenant, expense_account_id)
            expense.expense_account_source = ExpenseAccountSource.MANUAL_OVERRIDE

    if cost_center_id is not _OMIT:
        if cost_center_id is None:
            expense.cost_center = None
        else:
            expense.cost_center = load_bookable_cost_center(tenant, cost_center_id)

    update_fields = ['updated_at']
    if category_id is not _OMIT:
        update_fields.extend(['category', 'expense_account_source'])
    if expense_account_id is not _OMIT:
        update_fields.extend(['expense_account', 'expense_account_source'])
    if cost_center_id is not _OMIT:
        update_fields.append('cost_center')
    expense.save(update_fields=list(dict.fromkeys(update_fields)))
    expense.refresh_from_db()
    return expense_dto(expense)


@transaction.atomic
def approve_expense_for_posting(
    *,
    tenant,
    expense_id: int,
    user,
    settlement_method=_USE_EXISTING_SETTLEMENT_DEFAULT,
) -> dict:
    """draft → approved + post ``expense_approved`` (creates payable SubledgerItem).

    Idempotent when already approved with posted JE (heals missing subledger via post_document).

    ``settlement_method``:
    - omitted (default): legacy behaviour — if blank, set ``business_account``
    - explicit value (including ``''`` / ``None``): store that value; blank stays blank
    """
    expense = (
        Expense.all_objects.select_for_update()
        .filter(tenant=tenant, pk=expense_id)
        .select_related('supplier', 'category')
        .first()
    )
    if expense is None:
        from django.http import Http404

        raise Http404()

    if expense.status in ('rejected', 'cancelled'):
        raise ExpenseApproveConflict('invalid_status', 'Trošak nije u statusu koji se može odobriti.')

    if expense.status == 'draft':
        expense.status = 'approved'
        expense.approved_by = user if getattr(user, 'is_authenticated', False) else None
        if settlement_method is _USE_EXISTING_SETTLEMENT_DEFAULT:
            if not (expense.settlement_method or '').strip():
                expense.settlement_method = SettlementMethod.BUSINESS_ACCOUNT
        else:
            expense.settlement_method = (
                '' if settlement_method is None else str(settlement_method).strip()
            )
        update_fields = ['status', 'approved_by', 'settlement_method', 'updated_at']
        expense.save(update_fields=update_fields)
    elif expense.status not in ('approved', 'paid'):
        raise ExpenseApproveConflict('invalid_status', f'Nepodržan status: {expense.status}')

    if expense.supplier_id is None:
        raise ExpenseApproveBadRequest('missing_supplier', 'Trošak nema dobavljača.')

    post_document(tenant, expense, 'expense_approved', user)
    expense.refresh_from_db()
    return expense_dto(expense)
