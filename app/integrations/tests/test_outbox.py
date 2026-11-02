from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings

from fiscal_gateway.client.as4_client import As4SendError
from integrations.constants import IntegrationEnvironment, IntegrationProvider, IntegrationType
from integrations.manager import IntegrationManager
from integrations.models import IntegrationConfig, IntegrationOutboxMessage
from integrations.registry import discover_connectors
from integrations.retry_policy import is_retryable_exception
from integrations.services.outbox import enqueue_outbox, schedule_next_retry
from fiscal_gateway.models import DirectTenantConfig
from tenants.models import Tenant
from ubl.domain.errors import SemanticValidationError


class RetryPolicyTests(TestCase):
    def test_as4_error_is_retryable(self):
        self.assertTrue(is_retryable_exception(As4SendError('timeout')))

    def test_semantic_error_is_terminal(self):
        self.assertFalse(is_retryable_exception(SemanticValidationError(['bad oib'])))


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class OutboxEnqueueTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='outbox-test', name='Outbox Test')

    def test_enqueue_creates_message(self):
        msg = enqueue_outbox(
            tenant=self.tenant,
            operation=IntegrationOutboxMessage.OPERATION_SEND_ERACUN,
            provider=IntegrationProvider.DIRECT,
            correlation_id=uuid.uuid4(),
            last_error='network',
        )
        self.assertEqual(msg.status, IntegrationOutboxMessage.STATUS_PENDING)
        self.assertEqual(msg.attempt_count, 0)

    def test_schedule_next_retry_dead_after_max(self):
        msg = IntegrationOutboxMessage.all_objects.create(
            tenant=self.tenant,
            operation=IntegrationOutboxMessage.OPERATION_SEND_ERACUN,
            idempotency_key='test-key-dead',
            attempt_count=4,
            max_attempts=5,
        )
        schedule_next_retry(msg)
        msg.refresh_from_db()
        self.assertEqual(msg.status, IntegrationOutboxMessage.STATUS_DEAD)


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class ManagerOutboxHookTests(TestCase):
    def setUp(self):
        discover_connectors()
        self.tenant = Tenant.objects.create(slug='mgr-outbox', name='Mgr Outbox')
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

    @patch('fiscal_gateway.tasks.process_outbox_message_task')
    @patch('integrations.manager.enqueue_outbox')
    @patch('integrations.manager.IntegrationManager.get_connector')
    @patch('integrations.manager.EracunDocumentService.build_from_invoice')
    def test_transient_failure_enqueues_outbox(
        self, mock_build, mock_connector, mock_enqueue, mock_task,
    ):
        mock_build.return_value = (object(), '<Invoice/>')
        eracun = mock_connector.return_value
        eracun.send_outbound.side_effect = As4SendError('connection reset')
        invoice = type('Inv', (), {'tenant': self.tenant, 'pk': 1})()
        mock_enqueue.return_value = type('Outbox', (), {'pk': 99})()

        with self.assertRaises(As4SendError):
            IntegrationManager.send_eracun(invoice)

        mock_enqueue.assert_called_once()
        mock_task.apply_async.assert_called_once()
