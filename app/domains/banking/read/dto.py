"""DTO builders for banking read API — allowlisted fields only."""

from __future__ import annotations

from domains.banking.read.balances import (
    latest_statement_for_account,
    mask_iban,
    money,
    primary_connection_for_account,
    statement_closing_balance,
)
from domains.reporting.documents.snapshot import isoformat


def connection_summary(connection) -> dict | None:
    if connection is None:
        return None
    return {
        'id': connection.pk,
        'status': connection.status,
        'provider_code': connection.bank_provider.code if connection.bank_provider_id else None,
        'last_sync_at': isoformat(connection.last_sync_at) if connection.last_sync_at else None,
    }


def bank_account_dto(account, *, now=None) -> dict:
    connection = primary_connection_for_account(account)
    statement = latest_statement_for_account(account)
    balance = statement_closing_balance(
        account=account,
        statement=statement,
        connection=connection,
        now=now,
    )
    return {
        'id': account.pk,
        'account_name': account.account_name,
        'bank_name': account.bank_name,
        'account_number': account.account_number,
        'iban': account.iban or '',
        'currency': account.currency,
        'status': account.status,
        'is_active': account.is_active,
        'connection': connection_summary(connection),
        'balances': [balance] if balance else [],
    }


def statement_list_dto(statement) -> dict:
    return {
        'id': statement.pk,
        'statement_number': statement.statement_number,
        'bank_account_id': statement.bank_account_id,
        'statement_date': statement.statement_date.isoformat(),
        'opening_balance': money(statement.opening_balance),
        'closing_balance': money(statement.closing_balance),
        'status': statement.status,
        'currency': statement.bank_account.currency if statement.bank_account_id else None,
        'imported_at': isoformat(statement.imported_at) if statement.imported_at else None,
        'reconciled_at': isoformat(statement.reconciled_at) if statement.reconciled_at else None,
        'transaction_count': getattr(statement, 'transaction_count', None),
    }


def statement_detail_dto(statement, *, match_summary: dict) -> dict:
    payload = statement_list_dto(statement)
    payload['match_summary'] = match_summary
    return payload


def transaction_dto(tx) -> dict:
    return {
        'id': tx.pk,
        'bank_statement_id': tx.bank_statement_id,
        'bank_account_id': tx.bank_statement.bank_account_id if tx.bank_statement_id else None,
        'transaction_date': tx.transaction_date.isoformat(),
        'value_date': tx.value_date.isoformat() if tx.value_date else None,
        'amount': money(tx.amount),
        'currency': tx.currency,
        'transaction_type': tx.transaction_type,
        'description': tx.description,
        'reference': tx.reference,
        'counterparty_name': tx.counterparty_name,
        'counterparty_iban': tx.counterparty_iban,
        'external_id': tx.external_id,
        'match_status': tx.match_status,
        'matched_payment_id': tx.matched_payment_id,
        'matched_journal_entry_id': tx.matched_journal_entry_id,
    }


PAYMENT_ORDER_ALLOWLIST = frozenset({
    'id',
    'status',
    'amount',
    'currency',
    'debtor_iban',
    'creditor_iban',
    'creditor_name',
    'reference',
    'created_at',
})


def payment_order_dto(order) -> dict:
    """Strict allowlist — no SCA URLs, secrets, or internal errors."""
    return {
        'id': order.pk,
        'status': order.status,
        'amount': money(order.amount),
        'currency': order.currency,
        'debtor_iban': mask_iban(order.debtor_iban),
        'creditor_iban': mask_iban(order.creditor_iban),
        'creditor_name': order.creditor_name,
        'reference': order.reference,
        'created_at': isoformat(order.created_at) if order.created_at else None,
    }


def import_run_dto(run) -> dict:
    return {
        'id': run.pk,
        'run_uuid': str(run.run_uuid),
        'status': run.status,
        'source': run.source,
        'format': run.format,
        'original_filename': run.original_filename,
        'statements_processed': run.statements_processed,
        'statements_created': run.statements_created,
        'statements_updated': run.statements_updated,
        'transactions_processed': run.transactions_processed,
        'transactions_created': run.transactions_created,
        'transactions_skipped': run.transactions_skipped,
        'error_count': run.error_count,
        'warnings': list(run.warnings or []),
        'errors': list(run.errors or []),
        'created_at': isoformat(run.created_at) if run.created_at else None,
        'started_at': isoformat(run.started_at) if run.started_at else None,
        'finished_at': isoformat(run.finished_at) if run.finished_at else None,
    }


def sync_run_dto(run) -> dict:
    return {
        'id': run.pk,
        'connection_id': run.connection_id,
        'status': run.status,
        'transactions_created': run.transactions_created,
        'transactions_skipped': run.transactions_skipped,
        'last_error': run.last_error,
        'correlation_id': str(run.correlation_id),
        'created_at': isoformat(run.created_at) if run.created_at else None,
        'started_at': isoformat(run.started_at) if run.started_at else None,
        'finished_at': isoformat(run.finished_at) if run.finished_at else None,
    }
