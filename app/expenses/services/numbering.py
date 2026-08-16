from __future__ import annotations

from django.utils import timezone

from expenses.models import Expense


def assign_expense_number(tenant, *, year: int | None = None) -> str:
    """Interna numeracija troškova: T-YYYY-NNNN."""
    year = year or timezone.now().year
    prefix = f'T-{year}-'
    existing = (
        Expense.all_objects.filter(tenant=tenant, expense_number__startswith=prefix)
        .values_list('expense_number', flat=True)
    )
    max_seq = 0
    for number in existing:
        suffix = number[len(prefix):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f'{prefix}{max_seq + 1:04d}'
