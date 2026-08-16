"""Money helpers — Decimal quantization, no business rules."""

from decimal import ROUND_HALF_UP, Decimal

TWOPLACES = Decimal('0.01')


def quantize_money(value: Decimal | int | float | str) -> Decimal:
    """Round to two decimal places (HRK/EUR accounting precision)."""
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def zero_money() -> Decimal:
    return Decimal('0.00')


__all__ = ['TWOPLACES', 'quantize_money', 'zero_money']
