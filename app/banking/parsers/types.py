from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ParsedBankTransaction:
    external_id: str
    used_fallback_external_id: bool
    transaction_date: date
    value_date: date | None
    amount: Decimal
    transaction_type: str
    description: str
    reference: str
    counterparty_name: str
    counterparty_iban: str


@dataclass(frozen=True)
class ParsedBankStatement:
    iban: str
    currency: str
    statement_number: str
    statement_date: date
    opening_balance: Decimal
    closing_balance: Decimal
    transactions: tuple[ParsedBankTransaction, ...]
