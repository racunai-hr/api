"""Detect duplicate BankTransaction match targets (preflight for 0017)."""

from __future__ import annotations

from django.db.models import Count, QuerySet


def duplicate_matched_payment_groups(qs: QuerySet) -> list[dict]:
    return list(
        qs.filter(matched_payment_id__isnull=False)
        .values('matched_payment_id')
        .annotate(c=Count('id'))
        .filter(c__gt=1)
        .order_by('matched_payment_id')
    )


def duplicate_matched_journal_groups(qs: QuerySet) -> list[dict]:
    return list(
        qs.filter(matched_journal_entry_id__isnull=False)
        .values('matched_journal_entry_id')
        .annotate(c=Count('id'))
        .filter(c__gt=1)
        .order_by('matched_journal_entry_id')
    )


def raise_if_duplicate_bank_matches(qs: QuerySet) -> None:
    """Fail-closed guard used by migration 0017 and audits."""
    payment_groups = duplicate_matched_payment_groups(qs)
    journal_groups = duplicate_matched_journal_groups(qs)
    if not payment_groups and not journal_groups:
        return

    parts = []
    if payment_groups:
        parts.append(
            'matched_payment duplicates: '
            + ', '.join(
                f"payment_id={row['matched_payment_id']}(count={row['c']})"
                for row in payment_groups
            )
        )
    if journal_groups:
        parts.append(
            'matched_journal_entry duplicates: '
            + ', '.join(
                f"journal_entry_id={row['matched_journal_entry_id']}(count={row['c']})"
                for row in journal_groups
            )
        )
    raise RuntimeError(
        'BankTransaction match duplicates block migration 0017. '
        'Resolve before applying unique constraints. '
        + '; '.join(parts)
    )
