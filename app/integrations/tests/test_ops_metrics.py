from django.test import TestCase, override_settings

from integrations.audit import log_audit_step, new_correlation_id
from integrations.constants import IntegrationProvider
from integrations.models import IntegrationAuditLog
from integrations.services.ops_metrics import (
    errors_by_step,
    ops_dashboard_context,
    outbound_summary,
    schematron_skip_count,
)
from tenants.models import Tenant


@override_settings(TENANT_PLATFORM_DOMAIN='racunai.hr')
class OpsMetricsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='ops-test', name='Ops Test')
        cid = new_correlation_id()
        log_audit_step(
            tenant=self.tenant,
            step=IntegrationAuditLog.STEP_OUTBOUND_SENT,
            status=IntegrationAuditLog.STATUS_SUCCESS,
            correlation_id=cid,
            provider=IntegrationProvider.DIRECT,
            detail={'duration_ms': 120},
        )
        log_audit_step(
            tenant=self.tenant,
            step=IntegrationAuditLog.STEP_OUTBOUND_FAILED,
            status=IntegrationAuditLog.STATUS_FAILED,
            correlation_id=new_correlation_id(),
            provider=IntegrationProvider.DIRECT,
            detail={'messages': ['err']},
        )
        log_audit_step(
            tenant=self.tenant,
            step=IntegrationAuditLog.STEP_SCHEMATRON_SKIPPED,
            status=IntegrationAuditLog.STATUS_SKIPPED,
            correlation_id=new_correlation_id(),
            detail={'reason': 'no zip'},
        )

    def test_outbound_summary(self):
        rows = outbound_summary(tenant=self.tenant, days=7)
        providers = {row['provider']: row for row in rows}
        self.assertEqual(providers[IntegrationProvider.DIRECT]['sent'], 1)
        self.assertEqual(providers[IntegrationProvider.DIRECT]['failed'], 1)

    def test_ops_dashboard_context(self):
        ctx = ops_dashboard_context(tenant=self.tenant, days=7)
        self.assertIn('outbound', ctx)
        self.assertEqual(ctx['schematron_skipped'], 1)
        self.assertGreaterEqual(len(ctx['errors_by_step']), 1)

    def test_schematron_skip_count(self):
        self.assertEqual(schematron_skip_count(tenant=self.tenant, days=7), 1)
