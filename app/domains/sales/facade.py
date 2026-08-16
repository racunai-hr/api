"""Sales domain facade — public API.

Invoice lifecycle remains in ``invoices``; eRačun send delegates to Integration.
"""

from integrations.manager import IntegrationManager

MATURITY = 'L2'

__all__ = ['MATURITY', 'IntegrationManager']
