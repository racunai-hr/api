"""Resolve expense category vs expense GL account independently.

Category suggestion must not override an explicit user-selected category.
Account resolution never reads partner defaults — those pick a category first.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from accounting.models import ChartOfAccounts, PostingRule
from expenses.models import Expense, ExpenseAccountSource, ExpenseCategory, ExpensePostingProfile


class ExpenseAccountResolutionError(ValidationError):
    """Invalid explicit override (wrong tenant, inactive, or non-postable)."""


@dataclass(frozen=True)
class ResolvedExpenseAccount:
    account: ChartOfAccounts
    source: str
    warning: str | None = None


def is_expense_net_amount_rule(document_type: str, rule: PostingRule) -> bool:
    return document_type == 'expense_approved' and rule.amount_field == 'net_amount'


def account_is_usable(account: ChartOfAccounts | None, tenant) -> bool:
    return bool(
        account is not None
        and account.tenant_id == tenant.id
        and account.is_active
        and account.is_postable
    )


def load_tenant_category(tenant, category_id: int) -> ExpenseCategory:
    category = ExpenseCategory.all_objects.filter(tenant=tenant, pk=category_id).first()
    if category is None:
        raise ExpenseAccountResolutionError({'category_id': 'Vrsta troška nije pronađena.'})
    if not category.is_active:
        raise ExpenseAccountResolutionError({'category_id': 'Vrsta troška nije aktivna.'})
    return category


def load_postable_account(tenant, account_id: int, *, field: str = 'expense_account_id') -> ChartOfAccounts:
    account = ChartOfAccounts.all_objects.filter(tenant=tenant, pk=account_id).first()
    if account is None:
        raise ExpenseAccountResolutionError({field: 'Konto nije pronađeno.'})
    if not account_is_usable(account, tenant):
        raise ExpenseAccountResolutionError({
            field: 'Konto mora biti aktivno knjiživo konto ovog tenanta.',
        })
    return account


def resolve_expense_account(expense: Expense, posting_rule: PostingRule) -> ResolvedExpenseAccount:
    """Konto rashoda for the expense_approved net line.

    Priority:
    1. Expense.expense_account → manual_override (hard-fail if unusable)
    2. opex: Expense.category.default_account → category/partner source
       (asset_purchase skips this — keeps PostingRule debit, typically 0373)
    3. PostingRule debit → posting_rule_fallback
    """
    tenant = expense.tenant
    override = expense.expense_account
    if expense.expense_account_id:
        if not account_is_usable(override, tenant):
            raise ExpenseAccountResolutionError({
                'expense_account': 'Konto override mora biti aktivno knjiživo konto istog tenanta.',
            })
        return ResolvedExpenseAccount(
            account=override,
            source=ExpenseAccountSource.MANUAL_OVERRIDE,
        )

    profile = getattr(expense, 'posting_profile', None) or ExpensePostingProfile.OPEX
    if profile != ExpensePostingProfile.OPEX:
        fallback = _rule_fallback_account(tenant, posting_rule)
        return ResolvedExpenseAccount(
            account=fallback,
            source=ExpenseAccountSource.POSTING_RULE_FALLBACK,
        )

    category = getattr(expense, 'category', None)
    category_account = getattr(category, 'default_account', None) if category else None
    if category_account is not None and not account_is_usable(category_account, tenant):
        fallback = _rule_fallback_account(tenant, posting_rule)
        return ResolvedExpenseAccount(
            account=fallback,
            source=ExpenseAccountSource.POSTING_RULE_FALLBACK,
            warning='Vrsta troška nema valjano zadano konto; korišteno je pravilo knjiženja.',
        )
    if account_is_usable(category_account, tenant):
        stored = expense.expense_account_source
        if stored in {
            ExpenseAccountSource.PARTNER_DEFAULT,
            ExpenseAccountSource.PARTNER_HISTORY,
            ExpenseAccountSource.CATEGORY_DEFAULT,
        }:
            source = stored
        else:
            source = ExpenseAccountSource.CATEGORY_DEFAULT
        return ResolvedExpenseAccount(account=category_account, source=source)

    fallback = _rule_fallback_account(tenant, posting_rule)
    return ResolvedExpenseAccount(
        account=fallback,
        source=ExpenseAccountSource.POSTING_RULE_FALLBACK,
        warning='Vrsta troška nema zadano konto; korišteno je pravilo knjiženja.' if category else None,
    )


def _rule_fallback_account(tenant, posting_rule: PostingRule) -> ChartOfAccounts:
    code = posting_rule.debit_account_code
    account = ChartOfAccounts.all_objects.filter(tenant=tenant, account_code=code).first()
    if account is None:
        account = ChartOfAccounts.all_objects.filter(tenant=tenant, rrif_code=code).first()
    if account is None:
        raise ExpenseAccountResolutionError(f'Konto {code} ne postoji.')
    if not account_is_usable(account, tenant):
        raise ExpenseAccountResolutionError(
            f'Fallback konto {code} nije knjiživo.',
        )
    return account
