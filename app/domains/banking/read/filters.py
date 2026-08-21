"""Query filters and pagination for banking read API (ADR-0021)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


def _int(raw: str | None) -> int | None:
    if raw in (None, ''):
        return None
    return int(raw)


def _date(raw: str | None) -> date | None:
    if raw in (None, ''):
        return None
    return date.fromisoformat(raw)


def parse_page(query) -> tuple[int, int]:
    page = max(1, _int(query.get('page')) or 1)
    page_size = _int(query.get('page_size')) or 20
    page_size = min(max(1, page_size), 100)
    return page, page_size


@dataclass(frozen=True)
class StatementListFilters:
    bank_account_id: int | None = None
    status: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@dataclass(frozen=True)
class TransactionListFilters:
    bank_account_id: int | None = None
    statement_id: int | None = None
    match_status: str | None = None
    transaction_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@dataclass(frozen=True)
class PaymentOrderListFilters:
    status: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@dataclass(frozen=True)
class BankAccountListFilters:
    page: int = 1
    page_size: int = 20


def parse_statement_filters(query) -> StatementListFilters:
    status = query.get('status') or None
    if status and status not in ('imported', 'reconciled', 'archived'):
        raise ValueError('status mora biti imported, reconciled ili archived')
    page, page_size = parse_page(query)
    return StatementListFilters(
        bank_account_id=_int(query.get('bank_account')),
        status=status,
        date_from=_date(query.get('date_from')),
        date_to=_date(query.get('date_to')),
        page=page,
        page_size=page_size,
    )


def parse_transaction_filters(query) -> TransactionListFilters:
    match_status = query.get('match_status') or None
    if match_status and match_status not in ('unmatched', 'suggested', 'matched'):
        raise ValueError('match_status mora biti unmatched, suggested ili matched')
    transaction_type = query.get('transaction_type') or None
    if transaction_type and transaction_type not in ('debit', 'credit'):
        raise ValueError('transaction_type mora biti debit ili credit')
    page, page_size = parse_page(query)
    return TransactionListFilters(
        bank_account_id=_int(query.get('bank_account')),
        statement_id=_int(query.get('statement')),
        match_status=match_status,
        transaction_type=transaction_type,
        date_from=_date(query.get('date_from')),
        date_to=_date(query.get('date_to')),
        page=page,
        page_size=page_size,
    )


def parse_payment_order_filters(query) -> PaymentOrderListFilters:
    page, page_size = parse_page(query)
    return PaymentOrderListFilters(
        status=query.get('status') or None,
        date_from=_date(query.get('date_from')),
        date_to=_date(query.get('date_to')),
        page=page,
        page_size=page_size,
    )


def parse_bank_account_filters(query) -> BankAccountListFilters:
    page, page_size = parse_page(query)
    return BankAccountListFilters(page=page, page_size=page_size)
