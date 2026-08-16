from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from events.dispatcher import DomainEvent


@dataclass(frozen=True, kw_only=True)
class PaymentExecuted(DomainEvent):
    tenant_id: int
    payment_order_id: int
    payment_id: int | None
    bank_connection_id: int
    provider_code: str
    amount: Decimal
    currency: str
    execution_date: date
    bank_reference: str
    correlation_id: UUID | None
