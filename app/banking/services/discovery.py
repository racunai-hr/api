from __future__ import annotations

from django.utils import timezone

from banking.provider_models import BankConnection
from payments.models import BankAccount


def discover_accounts_from_api(
    connection: BankConnection,
    accounts: list[dict],
    *,
    primary_account: BankAccount | None = None,
) -> list[BankAccount]:
    """Populate BankAccount fields from GET /accounts API response."""
    now = timezone.now()
    discovered: list[BankAccount] = []
    primary = primary_account or connection.bank_account

    for index, raw in enumerate(accounts):
        resource_id = raw.get('resourceId') or raw.get('id') or ''
        iban = raw.get('iban') or raw.get('bban') or ''
        currency = raw.get('currency') or 'EUR'
        name = raw.get('name') or raw.get('product') or 'Bankovni račun'

        if index == 0:
            account = primary
        else:
            account_number = iban or resource_id or f'otp-{resource_id or index}'
            account, _ = BankAccount.all_objects.get_or_create(
                tenant=connection.tenant,
                account_number=account_number,
                defaults={
                    'account_name': name,
                    'bank_name': connection.bank_provider.name,
                    'iban': iban or None,
                    'currency': currency,
                    'status': 'pending_discovery',
                },
            )

        account.external_account_id = resource_id or account.external_account_id
        account.provider_connection = connection
        account.currency = currency
        if iban:
            account.iban = iban
        if name and account.account_name in ('', 'OTP račun', 'Bankovni račun'):
            account.account_name = name
        account.status = 'active'
        account.discovered_at = now
        account.is_active = True
        account.save(
            update_fields=[
                'external_account_id',
                'provider_connection',
                'currency',
                'iban',
                'account_name',
                'status',
                'discovered_at',
                'is_active',
            ],
        )
        discovered.append(account)

    return discovered


def get_sync_account_id(bank_account: BankAccount) -> str:
    account_id = bank_account.external_account_id
    if not account_id:
        raise ValueError('Nedostaje external account ID na bankovnom računu.')
    return account_id
