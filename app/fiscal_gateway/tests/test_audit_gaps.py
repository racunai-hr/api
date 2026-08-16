from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from fiscal_gateway.client.as4_client import As4ApplicationResponseResult
from fiscal_gateway.client.domibus_push import parse_domibus_push
from fiscal_gateway.services.inbound_as4 import handle_inbound_push
from fiscal_gateway.tests.test_as4_inbound import INVOICE_XML, _sample_push_soap, _wrap_invoice_sbdh
from integrations.constants import IntegrationEnvironment, IntegrationProvider, IntegrationType
from integrations.manager import IntegrationManager
from integrations.models import IntegrationAuditLog, IntegrationConfig
from integrations.registry import discover_connectors
from tenants.models import Tenant


@pytest.mark.django_db
class TestFiscalSubmitDemoAudit:
    @patch('fiscal_gateway.management.commands.fiscal_submit_demo.submit_outgoing_invoice')
    @patch('fiscal_gateway.management.commands.fiscal_submit_demo.get_active_fiscal_config')
    def test_dry_run_writes_fiscalized_audit(self, mock_config, mock_submit):
        tenant = Tenant.objects.create(slug='audit-cli', name='Audit CLI', is_active=True)
        config = MagicMock(tenant=tenant, cis_env='pts')
        mock_config.return_value = config
        log = MagicMock()
        log.STATUS_SUCCESS = 'success'
        log.status = 'success'
        log.jir = 'JIR-1'
        log.cis_request_id = 'req-1'
        log.error_code = ''
        log.error_message = ''
        log.pk = 42
        mock_submit.return_value = log

        out = StringIO()
        call_command('fiscal_submit_demo', tenant='audit-cli', dry_run=True, cis_env='pts', stdout=out)

        audit = IntegrationAuditLog.all_objects.get(tenant=tenant)
        assert audit.step == IntegrationAuditLog.STEP_FISCALIZED
        assert audit.status == IntegrationAuditLog.STATUS_SUCCESS
        assert 'correlation_id:' in out.getvalue()


@pytest.mark.django_db
class TestInboundAs4Audit:
    @patch('fiscal_gateway.services.inbound_as4.import_inbound_as4_expense')
    @patch('fiscal_gateway.services.inbound_as4.resolve_tenant_for_customer_oib')
    @patch('fiscal_gateway.services.inbound_as4.submit_application_response_via_domibus')
    def test_inbound_push_writes_audit_timeline(self, mock_submit, mock_tenant, mock_import, settings):
        settings.DOMIBUS_AP_OIB = '99999999994'
        tenant = Tenant.objects.create(slug='inbound-audit', name='Inbound Audit', is_active=True)
        mock_tenant.return_value = tenant
        mock_import.return_value = MagicMock(pk=7)
        mock_submit.return_value = As4ApplicationResponseResult(
            message_id='resp-audit@racunai.hr',
            domibus_response='<ok/>',
        )

        payload = _wrap_invoice_sbdh(INVOICE_XML.read_bytes())
        message = parse_domibus_push(_sample_push_soap(payload))
        handle_inbound_push(message)

        steps = list(
            IntegrationAuditLog.all_objects.filter(tenant=tenant)
            .order_by('created_at')
            .values_list('step', flat=True)
        )
        assert steps == [
            IntegrationAuditLog.STEP_INBOUND_RECEIVED,
            IntegrationAuditLog.STEP_EXPENSE_CREATED,
            IntegrationAuditLog.STEP_APP_RESPONSE_SENT,
        ]
        correlation_ids = IntegrationAuditLog.all_objects.filter(tenant=tenant).values_list(
            'correlation_id', flat=True
        )
        assert len(set(correlation_ids)) == 1


@pytest.mark.django_db
class TestManagerEnvironment:
    @patch('integrations.manager.EracunDocumentService.build_from_invoice')
    @patch('integrations.manager.IntegrationManager.get_connector')
    def test_send_eracun_uses_test_environment(self, mock_get_connector, mock_build):
        discover_connectors()
        tenant = Tenant.objects.create(slug='env-test', name='Env Test')
        IntegrationConfig.all_objects.create(
            tenant=tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            environment=IntegrationEnvironment.TEST,
            is_active=True,
        )
        mock_build.return_value = (MagicMock(), '<Invoice/>')
        eracun = MagicMock()
        mock_get_connector.side_effect = [eracun, None]
        invoice = MagicMock(tenant=tenant)

        IntegrationManager.send_eracun(invoice, environment=IntegrationEnvironment.TEST)

        mock_get_connector.assert_any_call(
            tenant,
            IntegrationType.ERACUN,
            environment=IntegrationEnvironment.TEST,
        )
        eracun.send_outbound.assert_called_once()
