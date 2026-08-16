from __future__ import annotations

from integrations.constants import IntegrationProvider, IntegrationType
from integrations.registry import register
from fiscal_gateway.adapters.ubl_to_fiscal import ubl_document_to_fiscal_payload
from fiscal_gateway.connector_eracun import DirectEracunConnector  # noqa: F401
from fiscal_gateway.connector_platform import FiskalPlatformFiscalizationConnector  # noqa: F401
from fiscal_gateway.services.outbound import submit_outgoing_invoice


@register(IntegrationType.FISCALIZATION, IntegrationProvider.CIS)
class CisFiscalizationConnector:
    def __init__(self, config):
        self.config = config

    @property
    def tenant(self):
        return self.config.tenant

    def fiscalize(self, invoice, document, ubl_xml: str, *, dry_run: bool = False, correlation_id=None):
        payload = ubl_document_to_fiscal_payload(document)
        return submit_outgoing_invoice(
            self.config,
            payload,
            invoice=invoice,
            dry_run=dry_run,
            correlation_id=correlation_id,
        )
