from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel, ConfigDict, Field

from ubl.domain.line import InvoiceLine
from ubl.domain.party import Party, SellerContact
from ubl.domain.totals import MonetaryTotal, TaxSubtotal
from ubl.hrcius.constants import (
    CREDIT_NOTE_TYPE_CODE,
    CUSTOMIZATION_ID,
    DEFAULT_CURRENCY,
    DEFAULT_INVOICE_TYPE_CODE,
    DEFAULT_PROFILE_ID,
)


class Delivery(BaseModel):
    model_config = ConfigDict(frozen=True)

    actual_delivery_date: date | None = None
    country_code: str = 'HR'


class PaymentMeans(BaseModel):
    model_config = ConfigDict(frozen=True)

    payment_means_code: str = '30'
    iban: str


class UblDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_number: str
    issue_date: date
    issue_time: time | None = None
    due_date: date | None = None
    invoice_type_code: str = DEFAULT_INVOICE_TYPE_CODE
    currency: str = DEFAULT_CURRENCY
    customization_id: str = CUSTOMIZATION_ID
    profile_id: str = DEFAULT_PROFILE_ID
    supplier: Party
    customer: Party
    seller_contact: SellerContact | None = None
    delivery: Delivery | None = None
    payment_means: PaymentMeans | None = None
    payment_terms_note: str | None = None
    lines: list[InvoiceLine] = Field(min_length=1)
    totals: MonetaryTotal
    tax_subtotals: list[TaxSubtotal] = Field(min_length=1)

    @property
    def is_credit_note(self) -> bool:
        return self.invoice_type_code == CREDIT_NOTE_TYPE_CODE
