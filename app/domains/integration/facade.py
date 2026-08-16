"""Integration domain facade — public API."""

from integrations.manager import IntegrationManager

MATURITY = 'L3'

__all__ = ['MATURITY', 'IntegrationManager']
