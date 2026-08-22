"""Approve OCR/manual expense drafts and post AP (expense_approved)."""

from __future__ import annotations

from django.db import transaction

from accounting.services.posting import post_document
from expenses.models import Expense, SettlementMethod

# Sentinel: omit ``settlement_method`` → keep legacy fill-in (blank → business_account).
_USE_EXISTING_SETTLEMENT_DEFAULT = object()


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
        'settlement_method': expense.settlement_method or '',
        'approved_by_id': expense.approved_by_id,
    }


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
