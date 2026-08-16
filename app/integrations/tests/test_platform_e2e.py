"""E2E tests for Fiskal Platform fiscalization connector."""

from __future__ import annotations

import json
from pathlib import Path
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
from fiscal_gateway.connector_platform import FiskalPlatformFiscalizationConnector
from fiscal_gateway.models import FiscalTenantConfig
from super_integration.models import SuperTenantConfig
from tenants.models import Tenant
from ubl.domain.document import UblDocument

GOLDEN_DIR = Path(__file__).resolve().parents[2] / 'ubl' / 'tests' / 'golden' / 'hrcius2025'


def _load_document(name: str) -> UblDocument:
    payload = json.loads((GOLDEN_DIR / 'fixtures' / f'{name}.json').read_text())
    return UblDocument.model_validate(payload)


@override_settings(
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    USE_FISKAL_PLATFORM=True,
    FISKAL_PLATFORM_URL='http://fiskal-api:8000',
    FISKAL_PLATFORM_API_TOKEN='test-token',
    FISKAL_PLATFORM_PROFILE_SLUG='finestar',
)
class FiskalPlatformConnectorE2ETests(TestCase):
    def setUp(self):
        discover_connectors()
        self.tenant = Tenant.objects.create(slug='e2e-platform', name='E2E Platform')
        FiscalTenantConfig.all_objects.create(
            tenant=self.tenant,
            oib='36619131370',
            is_active=True,
        )
        SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='user',
            password='pass',
            company_guid='guid-platform',
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )

    def test_fiskal_platform_connector_registered(self):
        self.assertIn(
            (IntegrationType.FISCALIZATION, IntegrationProvider.FISKAL_PLATFORM),
            registered_pairs(),
        )

    @patch('fiscal_gateway.connector_platform.FiscalPlatformClient')
    def test_platform_connector_dry_run(self, mock_client_cls):
        mock_client_cls.return_value.submit_and_wait.return_value = {
            'status': 'accepted',
            'jir': 'DRY-RUN-JIR',
            'dry_run': True,
        }
        config = MagicMock(tenant=self.tenant)
        connector = FiskalPlatformFiscalizationConnector(config)
        document = _load_document('invoice_minimal')

        log = connector.fiscalize(None, document, '<Invoice/>', dry_run=True)

        self.assertEqual(log.status, log.STATUS_SUCCESS)
        mock_client_cls.return_value.submit_and_wait.assert_called_once()

    @patch('integrations.manager.IntegrationManager._get_fiscal_connector')
    @patch('integrations.manager.EracunDocumentService.build_from_invoice')
    def test_manager_uses_platform_connector_when_flag_enabled(self, mock_build, mock_get_fiscal):
        invoice = MagicMock(tenant=self.tenant)
        document = MagicMock(spec=UblDocument)
        ubl_xml = '<Invoice/>'
        mock_build.return_value = (document, ubl_xml)

        eracun = MagicMock()
        fiscal = MagicMock()
        mock_get_fiscal.return_value = fiscal

        with patch.object(IntegrationManager, 'get_connector', return_value=eracun):
            IntegrationManager.send_eracun(invoice)

        mock_get_fiscal.assert_called_once_with(self.tenant, environment=IntegrationEnvironment.PRODUCTION)
        fiscal.fiscalize.assert_called_once()
