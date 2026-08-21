"""Shadow source selection — active legacy set plus reversals and their originals."""

from __future__ import annotations

from accounting.models import JournalEntry, JournalEntryLine, VATPeriod


def select_invoices(period: VATPeriod):
    from invoices.models import Invoice

    return list(
        Invoice.all_objects.filter(
            tenant_id=period.tenant_id,
            issue_date__year=period.year,
            issue_date__month=period.month,
            status__in=['sent', 'paid', 'overdue'],
        ).prefetch_related('items', 'company_to')
    )


def select_expenses(period: VATPeriod):
    from expenses.models import Expense

    return list(
        Expense.all_objects.filter(
            tenant_id=period.tenant_id,
            expense_date__year=period.year,
            expense_date__month=period.month,
            status__in=['approved', 'paid'],
        ).select_related('supplier')
    )


def select_posted_journal_lines(period: VATPeriod) -> list[JournalEntryLine]:
    return list(
        JournalEntryLine.objects.filter(
            journal_entry__tenant_id=period.tenant_id,
            journal_entry__status='posted',
            journal_entry__reversed_entry__isnull=True,
            journal_entry__entry_date__year=period.year,
            journal_entry__entry_date__month=period.month,
        ).select_related('journal_entry', 'account')
    )


def select_reversal_entries(period: VATPeriod) -> list[JournalEntry]:
    """Posted reversals in the period, including when the original is no longer posted."""
    return list(
        JournalEntry.all_objects.filter(
            tenant_id=period.tenant_id,
            status='posted',
            reversed_entry__isnull=False,
            entry_date__year=period.year,
            entry_date__month=period.month,
        ).select_related('reversed_entry')
    )
