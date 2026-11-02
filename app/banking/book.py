"""Knjigovodstveni iznos bankovne stavke.

`amount` i `currency` ostaju ono što je banka prijavila. U knjigu ide `amount_eur`
kad je poznat (HRK po fiksnom tečaju), inače sami iznos.
"""

from __future__ import annotations

from decimal import Decimal

from accounting.services.fx import to_eur_amount


def transaction_book_amount(bank_tx) -> Decimal:
    raw = bank_tx.amount_eur if bank_tx.amount_eur is not None else bank_tx.amount
    return abs(Decimal(raw)).quantize(Decimal('0.01'))


def transaction_book_currency(bank_tx) -> str:
    code = (bank_tx.currency or 'EUR').strip().upper() or 'EUR'
    if code != 'EUR' and bank_tx.amount_eur is not None:
        return 'EUR'
    return code


def eur_amount_for(amount, currency) -> Decimal | None:
    return to_eur_amount(amount, currency)
