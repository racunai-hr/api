from __future__ import annotations

import warnings

from integrations.constants import IntegrationProvider, IntegrationType
from integrations.registry import register
from super_integration.services.inbound import (
    approve_inbound_expense,
    reject_inbound_expense,
    sync_inbound_invoices,
)
from super_integration.services.outbound import (
    poll_outbound_statuses,
    report_invoice_payment,
    send_outbound_invoice,
)


@register(IntegrationType.ERACUN, IntegrationProvider.SUPER)
class SuperEracunConnector:
    """Deprecated — use fiscal_gateway.DirectEracunConnector (M1.7 rollback only)."""

    def __init__(self, config):
        warnings.warn(
            'SuperEracunConnector is deprecated; use DirectEracunConnector via '
            'IntegrationConfig(provider=direct). Set USE_SUPER_ERACUN_FALLBACK=True '
            'only for rollback.',
            DeprecationWarning,
            stacklevel=2,
        )
        self.config = config

    @property
    def tenant(self):
        return self.config.tenant

    def send_outbound(self, invoice, document, ubl_xml: str, *, correlation_id=None):
        return send_outbound_invoice(invoice, ubl_xml=ubl_xml, correlation_id=correlation_id)

    def sync_inbound(self) -> int:
        return sync_inbound_invoices(self.config)

    def poll_outbound_statuses(self) -> int:
        return poll_outbound_statuses(self.config)

    def report_payment(self, invoice):
        return report_invoice_payment(invoice)

    def approve_inbound(self, expense, user=None):
        return approve_inbound_expense(expense, user=user)

    def reject_inbound(self, expense, description: str = ''):
        return reject_inbound_expense(expense, description=description)
