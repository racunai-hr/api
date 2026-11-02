"""Računovodstveni tečaj. Nema mrežnog poziva prema HNB-u ni ECB-u.

Kasniji feed (HNB srednji tečaj kao zadani izvor, ECB samo kao PDV alternativa)
ulazi ovdje, bez promjene pozivatelja.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


# Zakonski fiksni tečaj uvođenja eura: 1 EUR = 7,53450 HRK.
HRK_PER_EUR = Decimal('7.53450')
_CENT = Decimal('0.01')


@dataclass(frozen=True)
class FxRate:
    rate: Decimal
    source: str
    rate_date: date


def resolve_fx_rate(
    tenant,
    currency,
    accounting_date,
    *,
    manual_rate=None,
    manual_date=None,
) -> FxRate | None:
    """Jedinica `currency` za 1 EUR. EUR je 1. Inače spremljeni ili ručni tečaj.

    HRK je zakonski tečaj uvođenja eura i ne traži ručni unos.
    Vraća None dok tečaj nije poznat. Pozivatelj onda pita korisnika.
    """
    del tenant  # tenant policy (HNB vs ECB) belongs to a future feed, not a literal.
    code = (currency or '').strip().upper()
    rate_date = manual_date or accounting_date or date.today()
    if code in ('', 'EUR'):
        return FxRate(rate=Decimal('1'), source='manual', rate_date=rate_date)
    if code == 'HRK':
        return FxRate(rate=HRK_PER_EUR, source='statutory', rate_date=rate_date)
    if manual_rate not in (None, ''):
        rate = Decimal(str(manual_rate))
        if rate > 0:
            return FxRate(rate=rate, source='manual', rate_date=rate_date)
    return None


def to_eur_amount(amount, currency) -> Decimal | None:
    """Knjigovodstveni iznos u EUR. HRK dijeli s 7,53450 i zaokružuje na cent."""
    rate = resolve_fx_rate(None, currency, None)
    if rate is None:
        return None
    value = Decimal(str(amount))
    return (value / rate.rate).quantize(_CENT, rounding=ROUND_HALF_UP)


def in_book_eur(amount, currency) -> Decimal:
    """Kuna postaje euro po fiksnom tečaju. Valuta bez tečaja ostaje nepromijenjena."""
    value = Decimal(str(amount or 0))
    converted = to_eur_amount(value, currency)
    return value if converted is None else converted
