from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class InvoiceLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_id: str
    quantity: Decimal
    unit_code: str = 'H87'
    line_extension_amount: Decimal
    item_name: str
    tax_category: str = 'S'
    tax_name: str
    tax_percent: Decimal
    unit_price: Decimal
    base_quantity: Decimal = Field(default=Decimal('1'))
    classification_code: str | None = None
    classification_scheme: str = 'CG'
