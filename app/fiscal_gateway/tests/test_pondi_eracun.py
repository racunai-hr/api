from __future__ import annotations

from datetime import date
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
from super_integration.models import SuperDocumentLink
from tenants.models import Tenant
from ubl.domain.document import UblDocument


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class PondiConnectorE2ETests(TestCase):
    def setUp(self):
        discover_connectors()
        self.tenant = Tenant.objects.create(slug='e2e-pondi', name='E2E Pondi')
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.PONDI,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )

    def test_pondi_eracun_registered(self):
        self.assertIn(
            (IntegrationType.ERACUN, IntegrationProvider.PONDI),
            registered_pairs(),
        )

    def test_manager_resolves_pondi_connector(self):
        connector = IntegrationManager.get_connector(
            self.tenant,
            IntegrationType.ERACUN,
            environment=IntegrationEnvironment.PRODUCTION,
        )
        self.assertEqual(connector.__class__.__name__, 'PondiEracunConnector')

    @patch('accounting.services.tax_projection.locks.lock_open_vat_period_for_source_mutation')
    @patch('fiscal_gateway.connector_pondi.taxpayer_oib_for_tenant', return_value='91381354893')
    @patch('fiscal_gateway.connector_pondi.GatewayV1Client')
    def test_pondi_outbound_uses_gateway_not_as4(self, mock_client_cls, _oib, _lock):
        from fiscal_gateway.connector_pondi import PondiEracunConnector

        mock_client_cls.return_value.send_outbound_document.return_value = {
            'document_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
            'exchange_status': 'SUBMITTED',
            'provider_refs': {'invoice_guid': '4242', 'unique_id': '4242'},
            'processing': {'state': None, 'reason': None},
        }
        connector = PondiEracunConnector(
            IntegrationConfig.all_objects.get(tenant=self.tenant)
        )
        invoice = MagicMock(tenant=self.tenant, pk=1, invoice_number='INV-001', issue_date=date(2026, 9, 1))
        invoice.assign_invoice_number = MagicMock()
        invoice.save = MagicMock()
        document = MagicMock(spec=UblDocument)
        document.is_credit_note = False

        link = connector.send_outbound(invoice, document, '<Invoice/>')

        mock_client_cls.return_value.send_outbound_document.assert_called_once()
        kwargs = mock_client_cls.return_value.send_outbound_document.call_args.kwargs
        self.assertEqual(kwargs['taxpayer_oib'], '91381354893')
        self.assertEqual(kwargs['document_type'], 'INVOICE')
        self.assertEqual(link.super_guid, '4242')
        self.assertTrue(
            SuperDocumentLink.all_objects.filter(
                tenant=self.tenant,
                direction=SuperDocumentLink.DIRECTION_OUTBOUND,
                super_guid='4242',
            ).exists()
        )
