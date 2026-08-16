from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from integrations.constants import (
    IntegrationEnvironment,
    IntegrationProvider,
    IntegrationType,
)
from integrations.manager import IntegrationManager
from integrations.models import IntegrationConfig
from integrations.registry import discover_connectors
from fiscal_gateway.models import DirectTenantConfig
from tenants.models import Tenant


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr', USE_FISKAL_PLATFORM=False)
class ManagerOrchestratorTests(TestCase):
    def setUp(self):
        discover_connectors()
        self.tenant = Tenant.objects.create(slug='mgr-test', name='Manager Test')
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

    @patch('integrations.manager.EracunDocumentService.build_from_invoice')
    @patch('integrations.manager.IntegrationManager.get_connector')
    def test_send_eracun_skips_fiscalization_when_no_fiscal_connector(
        self, mock_get_connector, mock_build,
    ):
        document = MagicMock()
        ubl_xml = '<Invoice/>'
        mock_build.return_value = (document, ubl_xml)
        eracun = MagicMock()
        mock_get_connector.side_effect = [eracun, None]
        invoice = MagicMock(tenant=self.tenant)

        IntegrationManager.send_eracun(invoice)

        mock_build.assert_called_once()
        self.assertIs(mock_build.call_args.args[0], invoice)
        self.assertTrue(mock_build.call_args.kwargs.get('sign'))
        eracun.send_outbound.assert_called_once()
        self.assertEqual(eracun.send_outbound.call_args.args[:3], (invoice, document, ubl_xml))

    @patch('integrations.manager.EracunDocumentService.build_from_invoice')
    @patch('integrations.manager.IntegrationManager.get_connector')
    def test_send_eracun_calls_fiscalization_when_connector_exists(
        self, mock_get_connector, mock_build,
    ):
        document = MagicMock()
        ubl_xml = '<Invoice/>'
        mock_build.return_value = (document, ubl_xml)
        eracun = MagicMock()
        fiscal = MagicMock()
        mock_get_connector.side_effect = [eracun, fiscal]
        invoice = MagicMock(tenant=self.tenant)

        IntegrationManager.send_eracun(invoice)

        eracun.send_outbound.assert_called_once()
        fiscal.fiscalize.assert_called_once()
        self.assertEqual(fiscal.fiscalize.call_args.args[:3], (invoice, document, ubl_xml))
