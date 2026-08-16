from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Address(BaseModel):
    model_config = ConfigDict(frozen=True)

    street: str
    city: str
    postal_zone: str
    country_code: str = 'HR'


class Contact(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = ''
    email: str = ''


class Party(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    oib: str
    address: Address
    party_identification: str | None = None
    contact: Contact | None = None
    company_legal_form: str | None = None


class SellerContact(BaseModel):
    model_config = ConfigDict(frozen=True)

    oib: str
    name: str
