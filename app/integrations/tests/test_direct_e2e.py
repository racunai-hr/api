from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from integrations.constants import (
    IntegrationEnvironment,
    IntegrationProvider,
    IntegrationType,
)
from integrations.manager import IntegrationManager
from integrations.models import IntegrationConfig
from integrations.registry import discover_connectors, registered_pairs
from fiscal_gateway.models import As4DocumentLink, DirectTenantConfig
from tenants.models import Tenant
from ubl.domain.document import UblDocument


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class DirectConnectorE2ETests(TestCase):
    def setUp(self):
        discover_connectors()
        self.tenant = Tenant.objects.create(slug='e2e-direct', name='E2E DIRECT')
        DirectTenantConfig.all_objects.create(
            tenant=self.tenant,
            oib='36619131370',
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )

    def test_direct_eracun_registered(self):
        self.assertIn(
            (IntegrationType.ERACUN, IntegrationProvider.DIRECT),
            registered_pairs(),
        )

    @patch('fiscal_gateway.services.outbound_eracun.submit_invoice_xml_via_domibus')
    def test_direct_outbound_send(self, mock_submit):
        from fiscal_gateway.client.as4_client import As4SendResult
        from fiscal_gateway.connector_eracun import DirectEracunConnector

        mock_submit.return_value = As4SendResult(
            message_id='msg-001@test.hr',
            invoice_id='INV-001',
            supplier_oib='12345678903',
            customer_oib='98765432108',
            domibus_response='<ok/>',
        )

        config = DirectTenantConfig.all_objects.get(tenant=self.tenant)
        connector = DirectEracunConnector(config)
        invoice = MagicMock(tenant=self.tenant, pk=1, invoice_number='INV-001')
        invoice.assign_invoice_number = MagicMock()
        invoice.save = MagicMock()
        document = MagicMock(spec=UblDocument)

        connector.send_outbound(invoice, document, '<Invoice/>')

        mock_submit.assert_called_once()
        self.assertTrue(
            As4DocumentLink.all_objects.filter(
                tenant=self.tenant,
                direction=As4DocumentLink.DIRECTION_OUTBOUND,
                message_id='msg-001@test.hr',
            ).exists()
        )
