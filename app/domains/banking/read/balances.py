"""Balance provenance helpers (ADR-0021 §10)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from banking.models import BankStatement
from banking.provider_models import BankConnection
from payments.models import BankAccount

STALE_SYNC_HOURS = 24
STALE_STATEMENT_DAYS = 7


def money(value: Decimal | None) -> str:
    if value is None:
        return '0.00'
    return f'{value:.2f}'


def mask_iban(iban: str | None) -> str:
    raw = (iban or '').replace(' ', '')
    if len(raw) <= 8:
        return raw
    return f'{raw[:4]}{"*" * (len(raw) - 8)}{raw[-4:]}'


def _as_of_from_date(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.get_current_timezone()).isoformat()


def statement_closing_balance(
    *,
    account: BankAccount,
    statement: BankStatement | None,
    connection: BankConnection | None,
    now: datetime | None = None,
) -> dict | None:
    if statement is None:
        return None
    now = now or timezone.now()
    today = timezone.localdate()
    stale_by_statement = statement.statement_date < (today - timedelta(days=STALE_STATEMENT_DAYS))
    stale_by_sync = False
    if connection is not None and connection.last_sync_at is not None:
        stale_by_sync = connection.last_sync_at < (now - timedelta(hours=STALE_SYNC_HOURS))
    return {
        'balance_type': 'statement-closing',
        'amount': money(statement.closing_balance),
        'currency': account.currency,
        'as_of': _as_of_from_date(statement.statement_date),
        'source': 'statement',
        'is_stale': bool(stale_by_statement or stale_by_sync),
    }


def latest_statement_for_account(account: BankAccount) -> BankStatement | None:
    return (
        BankStatement.all_objects.filter(tenant_id=account.tenant_id, bank_account=account)
        .order_by('-statement_date', '-id')
        .first()
    )


def primary_connection_for_account(account: BankAccount) -> BankConnection | None:
    if account.provider_connection_id:
        return (
            BankConnection.all_objects.filter(pk=account.provider_connection_id)
            .select_related('bank_provider')
            .first()
        )
    return (
        BankConnection.all_objects.filter(tenant_id=account.tenant_id, bank_account=account)
        .select_related('bank_provider')
        .order_by('-updated_at', '-id')
        .first()
    )
