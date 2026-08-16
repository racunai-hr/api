from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from banking.adapters.normalizer import to_bank_transactions
from banking.adapters.otp import get_adapter
from banking.models import BankStatement, BankTransaction
from banking.provider_models import BankConnection
from banking.reconciliation import create_payment_from_transaction, suggest_matches
from banking.services.discovery import get_sync_account_id


@transaction.atomic
def sync_connection_transactions(connection: BankConnection, *, days_back: int = 30) -> int:
    consent = connection.get_active_consent()
    if consent is None:
        raise ValueError('Nema aktivnog consenta.')

    account_id = get_sync_account_id(connection.bank_account)

    connection.status = 'syncing'
    connection.save(update_fields=['status', 'updated_at'])

    date_to = timezone.localdate()
    date_from = date_to - timedelta(days=days_back)
    adapter = get_adapter(connection)
    raw = adapter.fetch_transactions(account_id, date_from=date_from, date_to=date_to)
    normalized = to_bank_transactions(raw)

    User = get_user_model()
    system_user = User.objects.filter(is_superuser=True).first()
    if system_user is None:
        system_user = User.objects.first()

    statement_number = f'OTP-{account_id}-{date_to.isoformat()}'
    statement, _ = BankStatement.all_objects.get_or_create(
        tenant=connection.tenant,
        bank_account=connection.bank_account,
        statement_number=statement_number,
        defaults={
            'statement_date': date_to,
            'opening_balance': 0,
            'closing_balance': 0,
            'status': 'imported',
            'imported_by': system_user,
        },
    )

    created = 0
    for tx in normalized:
        _, was_created = BankTransaction.all_objects.get_or_create(
            tenant=connection.tenant,
            bank_statement=statement,
            external_id=tx.external_id,
            defaults={
                'transaction_date': tx.transaction_date,
                'value_date': tx.value_date,
                'amount': tx.amount,
                'currency': tx.currency,
                'transaction_type': tx.transaction_type,
                'description': tx.description,
                'reference': tx.reference,
                'counterparty_name': tx.counterparty_name,
                'counterparty_iban': tx.counterparty_iban,
            },
        )
        if was_created:
            created += 1

    connection.last_sync_at = timezone.now()
    connection.last_error = ''
    connection.status = 'connected'
    connection.sync_success_streak += 1
    connection.save(
        update_fields=[
            'last_sync_at',
            'last_error',
            'status',
            'sync_success_streak',
            'updated_at',
        ],
    )
    return created


def sync_and_reconcile(connection: BankConnection, *, auto_create_payments: bool = False) -> dict:
    created = sync_connection_transactions(connection)
    suggested = suggest_matches(connection.tenant)

    payments_created = 0
    if auto_create_payments:
        User = get_user_model()
        system_user = User.objects.filter(is_superuser=True).first()
        if system_user:
            for bank_tx in BankTransaction.all_objects.filter(
                tenant=connection.tenant,
                match_status='unmatched',
                bank_statement__bank_account=connection.bank_account,
            ):
                create_payment_from_transaction(bank_tx, system_user)
                payments_created += 1

    return {
        'transactions_synced': created,
        'suggestions': suggested,
        'payments_created': payments_created,
    }
