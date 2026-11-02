"""Suggest postable expense accounts from memory or the category default.

AI does not choose an account. The posting profile and PostingRule do that.
"""

from __future__ import annotations

from accounting.models import ChartOfAccounts
from domains.finance.services.posting_memory import expense_line_facts, load_remembered_lines
from domains.finance.services.posting_suggestions import suggest_expense_posting
from expenses.models import Expense


def suggest_expense_accounts(expense: Expense) -> dict:
    facts = expense_line_facts(expense)
    remembered = load_remembered_lines(expense)
    if remembered is not None:
        return {'remembered': True, 'lines': remembered}

    fallback = _fallback_account(expense)
    lines = []
    for index, (original, _normalized) in enumerate(facts, start=1):
        lines.append({
            'position': index,
            'description': original,
            'account_id': fallback.pk if fallback is not None else None,
            'account_code': fallback.account_code if fallback is not None else '',
            'account_name': fallback.account_name if fallback is not None else '',
            'source': 'category' if fallback is not None else 'none',
        })
    return {'remembered': False, 'lines': lines}


def _fallback_account(expense: Expense) -> ChartOfAccounts | None:
    suggestion = suggest_expense_posting(tenant=expense.tenant, expense=expense)
    if suggestion.account_id is None:
        return None
    account = ChartOfAccounts.all_objects.filter(
        pk=suggestion.account_id,
        tenant_id=expense.tenant_id,
    ).first()
    if account is None or not account.is_active or not account.is_postable:
        return None
    return account
