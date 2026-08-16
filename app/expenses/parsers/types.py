from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class NormalizedExpenseRow:
    row_number: int
    source: str
    external_id: str
    supplier_oib: str
    receipt_number: str
    expense_date: date
    amount: Decimal
    tax_amount: Decimal
    currency: str
    payment_method: str
    description: str
    raw_payload: dict
    jir: str = ''
    super_guid: str = ''
    supplier_name: str = ''
    due_date: date | None = None
    status: str = 'approved'


@dataclass(frozen=True)
class ParseRowError:
    row_number: int
    message: str


@dataclass(frozen=True)
class ParseResult:
    rows: tuple[NormalizedExpenseRow, ...]
    file_metadata: dict
    parse_errors: tuple[ParseRowError, ...]
