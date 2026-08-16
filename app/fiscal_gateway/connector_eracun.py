from __future__ import annotations

from integrations.constants import IntegrationProvider, IntegrationType
from integrations.registry import register
from fiscal_gateway.services.inbound_as4_actions import approve_inbound_expense, reject_inbound_expense
from fiscal_gateway.services.outbound_eracun import poll_outbound_statuses, send_outbound_invoice


@register(IntegrationType.ERACUN, IntegrationProvider.DIRECT)
class DirectEracunConnector:
    def __init__(self, config):
        self.config = config

    @property
    def tenant(self):
        return self.config.tenant

    def send_outbound(self, invoice, document, ubl_xml: str, *, correlation_id=None):
        return send_outbound_invoice(
            self.config,
            invoice,
            ubl_xml=ubl_xml,
            correlation_id=correlation_id,
        )

    def sync_inbound(self) -> int:
        return 0

    def poll_outbound_statuses(self) -> int:
        return poll_outbound_statuses(self.config)

    def report_payment(self, invoice):
        return None

    def approve_inbound(self, expense, user=None):
        return approve_inbound_expense(expense, user=user)

    def reject_inbound(self, expense, description: str = ''):
        return reject_inbound_expense(expense, description=description)
