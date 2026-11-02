"""Hrvatski format brojeva/datuma za tiskane obrasce plaće (server-side PDF).

NAPOMENA: ovo je isključivo za Django-rendered PDF predloške u ovom modulu
(WeasyPrint). Pravilo "HR date display" u app/README.md odnosi se na Next.js
korisničko sučelje (formatHr.ts) i ovdje se ne primjenjuje izravno — princip
(hr-HR prikaz, "—" za nedostupno) je isti.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter(name='hr_amount')
def hr_amount(value) -> str:
    if value is None or value == '':
        return '—'
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return '—'
    quantized = amount.quantize(Decimal('0.01'))
    sign = '-' if quantized < 0 else ''
    quantized = abs(quantized)
    whole, _, frac = f'{quantized:.2f}'.partition('.')
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}{'.'.join(groups)},{frac}"


@register.filter(name='hr_date')
def hr_date(value) -> str:
    if not value:
        return '—'
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return '—'
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime('%d.%m.%Y.')


@register.filter(name='hr_percent')
def hr_percent(value) -> str:
    if value is None:
        return '—'
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return '—'
    return f"{hr_amount(amount)}%"
