from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable


@runtime_checkable
class BankAdapter(Protocol):
    def fetch_transactions(
        self,
        account_id: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]: ...


@dataclass(frozen=True)
class NormalizedTransaction:
    external_id: str
    transaction_date: date
    value_date: date | None
    amount: Decimal
    currency: str
    transaction_type: str
    description: str
    reference: str
    counterparty_name: str
    counterparty_iban: str
