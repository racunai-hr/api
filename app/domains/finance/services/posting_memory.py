"""Remember the expense accounts used for one supplier and one line signature."""

from __future__ import annotations

import hashlib

from accounting.models import ChartOfAccounts
from domains.finance.services.account_resolver import account_is_usable, load_postable_account
from expenses.models import Expense, ExpenseAccountSource, SupplierLinePostingPattern


def normalize_line_text(value: str | None) -> str:
    return ' '.join((value or '').casefold().split())[:240]


def expense_line_facts(expense: Expense) -> list[tuple[str, str]]:
    """Original text and the normalized key, in line order.

    A draft without lines uses the header description as the single line.
    """
    rows = list(expense.lines.order_by('position', 'id'))
    if rows:
        return [(row.description, normalize_line_text(row.description)) for row in rows]
    text = expense.description or ''
    return [(text, normalize_line_text(text))]


def line_signature(parts: list[str]) -> str:
    return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()


def load_remembered_lines(expense: Expense) -> list[dict] | None:
    if expense.supplier_id is None:
        return None
    facts = expense_line_facts(expense)
    parts = [normalized for _, normalized in facts]
    pattern = SupplierLinePostingPattern.all_objects.filter(
        tenant_id=expense.tenant_id,
        supplier_id=expense.supplier_id,
        line_signature=line_signature(parts),
    ).first()
    if pattern is None or not isinstance(pattern.accounts, list):
        return None
    if len(pattern.accounts) != len(facts):
        return None
    lines: list[dict] = []
    for index, (original, normalized) in enumerate(facts, start=1):
        row = pattern.accounts[index - 1]
        if not isinstance(row, dict) or row.get('description') != normalized:
            return None
        try:
            account_id = int(row.get('account_id'))
        except (TypeError, ValueError):
            return None
        account = ChartOfAccounts.all_objects.filter(
            pk=account_id,
            tenant_id=expense.tenant_id,
        ).first()
        if not account_is_usable(account, expense.tenant):
            return None
        lines.append({
            'position': index,
            'description': original,
            'account_id': account.pk,
            'account_code': account.account_code,
            'account_name': account.account_name,
            'source': 'remembered',
        })
    return lines


def remember_line_accounts(expense: Expense, account_ids: dict[int, int]) -> None:
    facts = expense_line_facts(expense)
    parts = [normalized for _, normalized in facts]
    payload = []
    for index, (_, normalized) in enumerate(facts, start=1):
        if index not in account_ids:
            raise ValueError('Svaka stavka mora imati konto.')
        payload.append({
            'position': index,
            'description': normalized,
            'account_id': int(account_ids[index]),
        })
    SupplierLinePostingPattern.all_objects.update_or_create(
        tenant_id=expense.tenant_id,
        supplier_id=expense.supplier_id,
        line_signature=line_signature(parts),
        defaults={'accounts': payload},
    )


def apply_line_accounts(expense: Expense, account_ids: dict[int, int]) -> None:
    facts = expense_line_facts(expense)
    if set(account_ids) != set(range(1, len(facts) + 1)):
        raise ValueError('Konta moraju pokriti sve stavke.')
    rows = list(expense.lines.order_by('position', 'id'))
    if rows:
        for index, line in enumerate(rows, start=1):
            account = load_postable_account(expense.tenant, account_ids[index], field='lines')
            line.posting_account = account
            line.save(update_fields=['posting_account', 'updated_at'])
        return
    account = load_postable_account(expense.tenant, account_ids[1], field='expense_account_id')
    expense.expense_account = account
    expense.expense_account_source = ExpenseAccountSource.MANUAL_OVERRIDE
    expense.save(update_fields=['expense_account', 'expense_account_source', 'updated_at'])


def remembered_account_ids(expense: Expense) -> dict[int, int] | None:
    lines = load_remembered_lines(expense)
    if lines is None:
        return None
    return {row['position']: row['account_id'] for row in lines}
