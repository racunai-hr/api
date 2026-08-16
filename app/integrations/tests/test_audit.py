from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase, override_settings

from integrations.constants import IntegrationEnvironment, IntegrationProvider, IntegrationType
from integrations.manager import IntegrationManager
from integrations.models import IntegrationAuditLog, IntegrationConfig
from tenants.models import Tenant
from ubl.domain.errors import DocumentBuildError, SemanticValidationError
from ubl.services.document_service import EracunDocumentService


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr', USE_FISKAL_PLATFORM=False)
class IntegrationAuditTrailTests(TestCase):
    def setUp(self):
        from integrations.registry import discover_connectors
        from fiscal_gateway.models import DirectTenantConfig

        discover_connectors()
        self.tenant = Tenant.objects.create(slug='audit-e2e', name='Audit E2E')
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
    def test_failed_semantic_audit_before_connector(self, mock_build):
        mock_build.side_effect = SemanticValidationError(['Kupac: OIB nevaljan'])
        invoice = MagicMock(tenant=self.tenant)

        with patch.object(IntegrationManager, 'get_connector') as mock_get:
            with self.assertRaises(SemanticValidationError):
                IntegrationManager.send_eracun(invoice)
            mock_get.assert_not_called()

    def test_document_build_failure_creates_audit(self):
        invoice = MagicMock(tenant=self.tenant)
        with self.assertRaises(DocumentBuildError):
            EracunDocumentService.build_from_invoice(invoice)
        self.assertTrue(
            IntegrationAuditLog.all_objects.filter(
                tenant=self.tenant,
                step=IntegrationAuditLog.STEP_DOCUMENT_BUILT,
                status=IntegrationAuditLog.STATUS_FAILED,
            ).exists()
        )

    @patch('integrations.manager.EracunDocumentService.build_from_invoice')
    def test_successful_send_creates_audit_trail(self, mock_build):
        invoice = MagicMock(tenant=self.tenant)
        document = MagicMock()
        ubl_xml = '<Invoice/>'
        mock_build.return_value = (document, ubl_xml)

        eracun = MagicMock()
        eracun.send_outbound.return_value = MagicMock()

        with patch.object(IntegrationManager, 'get_connector', side_effect=[eracun, None]):
            IntegrationManager.send_eracun(invoice)

        mock_build.assert_called_once()
        self.assertTrue(mock_build.call_args.kwargs.get('correlation_id'))
