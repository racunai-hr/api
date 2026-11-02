"""List/update tenant expense categories (vrsta troška)."""

from __future__ import annotations

from django.http import Http404

from domains.finance.services.account_resolver import load_postable_account
from domains.finance.services.cost_center_resolver import load_bookable_cost_center
from domains.finance.services.cost_centers import cost_center_ref
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
        'default_cost_center': cost_center_ref(category.default_cost_center),
    }


def list_expense_categories(*, tenant) -> dict:
    rows = (
        ExpenseCategory.all_objects.filter(tenant=tenant, is_active=True)
        .select_related('default_account', 'default_cost_center')
        .order_by('name')
    )
    results = [category_dto(row) for row in rows]
    return {'count': len(results), 'results': results}


_UNSET = object()


def update_expense_category_default_account(
    *,
    tenant,
    category_id: int,
    default_account_id: int | None,
) -> dict:
    return update_expense_category(
        tenant=tenant,
        category_id=category_id,
        default_account_id=default_account_id,
    )


def update_expense_category(
    *,
    tenant,
    category_id: int,
    default_account_id=_UNSET,
    default_cost_center_id=_UNSET,
) -> dict:
    category = ExpenseCategory.all_objects.filter(tenant=tenant, pk=category_id).first()
    if category is None:
        raise Http404()
    update_fields = []
    if default_account_id is not _UNSET:
        if default_account_id is None:
            category.default_account = None
        else:
            category.default_account = load_postable_account(
                tenant, default_account_id, field='default_account_id',
            )
        update_fields.append('default_account')
    if default_cost_center_id is not _UNSET:
        if default_cost_center_id is None:
            category.default_cost_center = None
        else:
            category.default_cost_center = load_bookable_cost_center(
                tenant, default_cost_center_id, field='default_cost_center_id',
            )
        update_fields.append('default_cost_center')
    if update_fields:
        category.save(update_fields=update_fields)
    category.refresh_from_db()
    return category_dto(category)
