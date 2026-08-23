"""Read-only tenant chart of accounts for posting pickers."""

from __future__ import annotations

from django.db.models import Q

from accounting.models import ChartOfAccounts

_MAX_RESULTS = 500


def list_postable_accounts(*, tenant, search: str = '') -> dict:
    qs = ChartOfAccounts.all_objects.filter(
        tenant=tenant,
        is_active=True,
        is_postable=True,
    )
    term = (search or '').strip()
    if term:
        qs = qs.filter(Q(account_code__icontains=term) | Q(account_name__icontains=term))
    rows = list(qs.order_by('account_code')[:_MAX_RESULTS])
    results = [
        {
            'id': row.pk,
            'code': row.account_code,
            'name': row.account_name,
            'active': row.is_active,
        }
        for row in rows
    ]
    return {'count': len(results), 'results': results}
