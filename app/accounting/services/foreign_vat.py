"""Lokalni strani PDV i preračun u EUR iz jednog FX snapshota.

Strani iznosi ostaju u valuti dokumenta. Knjiženje:
  EUR gross = round(foreign_gross / rate)
  EUR VAT   = round(foreign_vat / rate)
  EUR net   = EUR gross - EUR VAT
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')
TREATMENTS = ('expense', 'refund_receivable')
ZERO = {
    'cost': Decimal('0.00'),
    'refund': Decimal('0.00'),
    'gross': Decimal('0.00'),
}


def money(value) -> Decimal:
    if value is None or value == '':
        return Decimal('0.00')
    return Decimal(value)


def has_foreign_local_vat(source) -> bool:
    """Gate: obračunan strani PDV, ne valuta i ne samo zemlja dobavljača."""
    if source is None:
        return False
    country = (getattr(source, 'foreign_vat_country', '') or '').strip().upper()
    return money(getattr(source, 'foreign_vat_amount', None)) > 0 and country not in ('', 'HR')


def eur_split(gross, vat, rate, treatment: str) -> dict[str, Decimal]:
    """Oba tretmana dijele isti snapshot. Neto se ne zaokružuje iz izvorne valute."""
    fx = Decimal(rate)
    gross_eur = (money(gross) / fx).quantize(CENT, rounding=ROUND_HALF_UP)
    vat_eur = (money(vat) / fx).quantize(CENT, rounding=ROUND_HALF_UP)
    net_eur = gross_eur - vat_eur
    if treatment == 'refund_receivable':
        return {'cost': net_eur, 'refund': vat_eur, 'gross': gross_eur}
    return {'cost': gross_eur, 'refund': Decimal('0.00'), 'gross': gross_eur}


def foreign_posting_amounts(source) -> dict[str, Decimal] | None:
    """None kad dokument nema lokalni strani PDV.

    Kad ga ima, a tretman ili tečaj još nedostaju, iznosi su 0 da se valuta
    dokumenta ne proknjiži kao euro.
    """
    if not has_foreign_local_vat(source):
        return None
    treatment = getattr(source, 'foreign_vat_treatment', '') or ''
    rate = getattr(source, 'fx_rate', None)
    if treatment not in TREATMENTS or rate is None or Decimal(rate) <= 0:
        return dict(ZERO)
    gross = getattr(source, 'foreign_gross_amount', None)
    if gross is None:
        gross = getattr(source, 'amount', None)
    return eur_split(
        gross,
        getattr(source, 'foreign_vat_amount', None),
        rate,
        treatment,
    )
