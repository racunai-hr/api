from __future__ import annotations

from integrations.constants import IntegrationProvider, IntegrationType
from integrations.registry import register


@register(IntegrationType.PAYMENT, IntegrationProvider.OTP)
class OtpPaymentConnector:
    def __init__(self, provider_config):
        self.provider_config = provider_config

    def sync_statements(self) -> int:
        from banking.services.sync import sync_connection_transactions

        connection = self.provider_config.connection
        return sync_connection_transactions(connection)

    def get_active_connection(self):
        return self.provider_config.connection
