"""List/update tenant expense categories (vrsta troška)."""

from __future__ import annotations

from django.http import Http404

from domains.finance.services.account_resolver import load_postable_account
from expenses.models import ExpenseCategory


def _account_ref(account) -> dict | None:
    if account is None:
        return None
    return {
        'id': account.pk,
        'code': account.account_code,
        'name': account.account_name,
        'active': account.is_active,
    }


def category_dto(category: ExpenseCategory) -> dict:
    return {
        'id': category.pk,
        'name': category.name,
        'code': category.code,
        'is_active': category.is_active,
        'default_account': _account_ref(category.default_account),
    }


def list_expense_categories(*, tenant) -> dict:
    rows = (
        ExpenseCategory.all_objects.filter(tenant=tenant, is_active=True)
        .select_related('default_account')
        .order_by('name')
    )
    results = [category_dto(row) for row in rows]
    return {'count': len(results), 'results': results}


def update_expense_category_default_account(
    *,
    tenant,
    category_id: int,
    default_account_id: int | None,
) -> dict:
    category = ExpenseCategory.all_objects.filter(tenant=tenant, pk=category_id).first()
    if category is None:
        raise Http404()
    if default_account_id is None:
        category.default_account = None
    else:
        category.default_account = load_postable_account(
            tenant, default_account_id, field='default_account_id',
        )
    category.save(update_fields=['default_account'])
    category.refresh_from_db()
    return category_dto(category)
