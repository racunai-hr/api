from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class TaxSubtotal(BaseModel):
    model_config = ConfigDict(frozen=True)

    taxable_amount: Decimal
    tax_amount: Decimal
    category_id: str = 'S'
    percent: Decimal


class MonetaryTotal(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_extension_amount: Decimal
    tax_exclusive_amount: Decimal
    tax_inclusive_amount: Decimal
    payable_amount: Decimal
