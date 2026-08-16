"""Kloniranje RRIF kontnog plana za tenant."""

from __future__ import annotations

from django.db import transaction

from accounting.models import AccountType, ChartOfAccounts, RRIFChartEntry
from accounting.rrif import RRIF_CLASS_TO_ACCOUNT_TYPE


def ensure_account_types(tenant) -> dict[str, AccountType]:
    types = {}
    for name, _label in AccountType.TYPE_CHOICES:
        obj, _ = AccountType.all_objects.get_or_create(
            tenant=tenant,
            name=name,
            defaults={'description': ''},
        )
        types[name] = obj
    return types


@transaction.atomic
def provision_tenant_chart(tenant, *, update_names: bool = False) -> int:
    """Kopira platform RRIF u tenant ChartOfAccounts. Vraća broj novih konta."""
    entries = list(RRIFChartEntry.objects.order_by('code'))
    if not entries:
        return 0

    account_types = ensure_account_types(tenant)
    created = 0
    code_to_account: dict[str, ChartOfAccounts] = {}

    existing = {
        a.rrif_code: a
        for a in ChartOfAccounts.all_objects.filter(tenant=tenant, is_rrif=True)
        if a.rrif_code
    }

    for entry in entries:
        account_type = account_types.get(
            entry.account_type_name,
            account_types[RRIF_CLASS_TO_ACCOUNT_TYPE.get(entry.account_class, 'asset')],
        )
        if entry.code in existing:
            account = existing[entry.code]
            if update_names:
                account.account_name = entry.name
                account.is_synthetic = entry.is_synthetic
                account.is_postable = entry.is_postable
                account.level = entry.level
                account.account_class = entry.account_class
                account.save(
                    update_fields=[
                        'account_name', 'is_synthetic', 'is_postable',
                        'level', 'account_class',
                    ]
                )
            code_to_account[entry.code] = account
            continue

        account = ChartOfAccounts.all_objects.create(
            tenant=tenant,
            account_code=entry.code,
            rrif_code=entry.code,
            account_name=entry.name,
            account_type=account_type,
            account_class=entry.account_class,
            level=entry.level,
            is_synthetic=entry.is_synthetic,
            is_postable=entry.is_postable,
            is_rrif=True,
        )
        code_to_account[entry.code] = account
        existing[entry.code] = account
        created += 1

    for entry in entries:
        if not entry.parent_code:
            continue
        account = code_to_account.get(entry.code)
        parent = code_to_account.get(entry.parent_code)
        if account and parent and account.parent_account_id != parent.id:
            account.parent_account = parent
            account.save(update_fields=['parent_account'])

    return created
