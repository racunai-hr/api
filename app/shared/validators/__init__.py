"""Generic validators — compose shared/oib and shared/iban."""

from shared.iban import normalize_iban
from shared.oib import normalize_oib

__all__ = ['normalize_oib', 'normalize_iban']
