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
from fiscal_gateway.connector import CisFiscalizationConnector
from fiscal_gateway.models import FiscalTenantConfig
from super_integration.models import SuperTenantConfig
from tenants.models import Tenant
from ubl.domain.document import UblDocument

GOLDEN_DIR = Path(__file__).resolve().parents[2] / 'ubl' / 'tests' / 'golden' / 'hrcius2025'


def _load_document(name: str) -> UblDocument:
    payload = json.loads((GOLDEN_DIR / 'fixtures' / f'{name}.json').read_text())
    return UblDocument.model_validate(payload)


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr', USE_FISKAL_PLATFORM=False)
class SuperOutboundE2ETests(TestCase):
    def setUp(self):
        discover_connectors()
        self.tenant = Tenant.objects.create(slug='e2e-super', name='E2E SUPER')
        SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='user',
            password='pass',
            company_guid='guid-e2e',
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )

    @patch('integrations.manager.EracunDocumentService.build_from_invoice')
    def test_super_outbound_validates_ubl_before_send(self, mock_build):
        invoice = MagicMock(tenant=self.tenant)
        document = MagicMock(spec=UblDocument)
        ubl_xml = '<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
        mock_build.return_value = (document, ubl_xml)

        eracun = MagicMock()
        with patch.object(IntegrationManager, 'get_connector', side_effect=[eracun, None]):
            IntegrationManager.send_eracun(invoice)

        mock_build.assert_called_once()
        self.assertIs(mock_build.call_args.args[0], invoice)
        eracun.send_outbound.assert_called_once()
        self.assertEqual(eracun.send_outbound.call_args.args[:3], (invoice, document, ubl_xml))


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr', USE_FISKAL_PLATFORM=False)
class DualConnectorE2ETests(TestCase):
    def setUp(self):
        discover_connectors()
        self.tenant = Tenant.objects.create(slug='e2e-dual', name='E2E Dual')
        SuperTenantConfig.all_objects.create(
            tenant=self.tenant,
            username='user',
            password='pass',
            company_guid='guid-dual',
            is_active=True,
        )
        FiscalTenantConfig.all_objects.create(
            tenant=self.tenant,
            oib='36619131370',
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.SUPER,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )
        IntegrationConfig.all_objects.create(
            tenant=self.tenant,
            integration_type=IntegrationType.FISCALIZATION,
            provider=IntegrationProvider.CIS,
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        )

    @patch('integrations.manager.EracunDocumentService.build_from_invoice')
    def test_dual_connector_orchestrator_calls_both(self, mock_build):
        invoice = MagicMock(tenant=self.tenant)
        document = MagicMock(spec=UblDocument)
        ubl_xml = '<Invoice/>'
        mock_build.return_value = (document, ubl_xml)

        eracun = MagicMock()
        fiscal = MagicMock()

        def get_connector_side_effect(t, itype, environment=IntegrationEnvironment.PRODUCTION):
            if itype == IntegrationType.ERACUN:
                return eracun
            if itype == IntegrationType.FISCALIZATION:
                return fiscal
            return None

        with patch.object(IntegrationManager, 'get_connector', side_effect=get_connector_side_effect):
            IntegrationManager.send_eracun(invoice)

        eracun.send_outbound.assert_called_once()
        fiscal.fiscalize.assert_called_once()
        self.assertEqual(eracun.send_outbound.call_args.args[:3], (invoice, document, ubl_xml))
        self.assertEqual(fiscal.fiscalize.call_args.args[:3], (invoice, document, ubl_xml))


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class CisConnectorE2ETests(TestCase):
    def setUp(self):
        discover_connectors()

    def test_cis_fiscalization_registered(self):
        self.assertIn(
            (IntegrationType.FISCALIZATION, IntegrationProvider.CIS),
            registered_pairs(),
        )

    @patch('fiscal_gateway.connector.submit_outgoing_invoice')
    def test_cis_fiscalize_dry_run(self, mock_submit):
        mock_submit.return_value = MagicMock()
        tenant = Tenant.objects.create(slug='e2e-cis', name='E2E CIS')
        config = FiscalTenantConfig.all_objects.create(
            tenant=tenant,
            oib='36619131370',
            is_active=True,
        )
        document = _load_document('invoice_minimal')
        connector = CisFiscalizationConnector(config)
        invoice = MagicMock(tenant=tenant)

        connector.fiscalize(invoice, document, '<Invoice/>', dry_run=True)

        mock_submit.assert_called_once()
        self.assertTrue(mock_submit.call_args.kwargs['dry_run'])
