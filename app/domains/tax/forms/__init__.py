"""Tax forms — PDV, PDV-S, ZP, JOPPD, …

Implementations live in accounting/services/tax_forms/ during migration.
New forms implement TaxFormEngine (protocol.py).
"""

from domains.tax.forms.protocol import TaxFormEngine

__all__ = ['TaxFormEngine']
