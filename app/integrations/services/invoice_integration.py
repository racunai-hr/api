from __future__ import annotations

from integrations.manager import IntegrationManager


class InvoiceIntegrationService:
    """Facade for invoice-related integration operations."""

    @staticmethod
    def send_eracun(invoice, *, triggered_by=None, environment=None):
        from integrations.constants import IntegrationEnvironment

        env = environment or IntegrationEnvironment.PRODUCTION
        return IntegrationManager.send_eracun(
            invoice,
            triggered_by=triggered_by,
            environment=env,
        )

    @staticmethod
    def report_payment(invoice):
        return IntegrationManager.report_invoice_payment(invoice)
