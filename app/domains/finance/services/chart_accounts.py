"""Read-only tenant chart of accounts for posting pickers."""

from __future__ import annotations

from django.db.models import Q

from accounting.models import ChartOfAccounts

_MAX_RESULTS = 500


def list_postable_accounts(*, tenant, search: str = '') -> dict:
    """Return postable accounts for posting pickers.

    ``count`` is the total number of matching rows after filters, before
    the server-side ``_MAX_RESULTS`` slice. ``results`` is at most that
    slice, so ``count`` may be larger than ``len(results)``.
    """
    qs = ChartOfAccounts.all_objects.filter(
        tenant=tenant,
        is_active=True,
        is_postable=True,
    )
    term = (search or '').strip()
    if term:
        qs = qs.filter(Q(account_code__icontains=term) | Q(account_name__icontains=term))
    count = qs.count()
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
    return {'count': count, 'results': results}
