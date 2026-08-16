from __future__ import annotations

import time
import uuid
from typing import Any

from integrations.models import IntegrationAuditLog


def new_correlation_id() -> uuid.UUID:
    return uuid.uuid4()


def _maybe_run_alerts(tenant, step: str, status: str) -> None:
    if status != IntegrationAuditLog.STATUS_FAILED and step != IntegrationAuditLog.STEP_SCHEMATRON_SKIPPED:
        return
    if step not in (
        IntegrationAuditLog.STEP_OUTBOUND_FAILED,
        IntegrationAuditLog.STEP_FISCAL_FAILED,
        IntegrationAuditLog.STEP_SCHEMATRON_SKIPPED,
    ):
        return
    try:
        from integrations.services.alerting import run_integration_alerts

        run_integration_alerts(tenant=tenant)
    except Exception:
        pass


def log_audit_step(
    *,
    tenant,
    step: str,
    status: str,
    correlation_id: uuid.UUID,
    invoice=None,
    integration_type: str = '',
    provider: str = '',
    detail: dict[str, Any] | None = None,
    triggered_by=None,
    super_link=None,
    fiscal_log=None,
    as4_link=None,
) -> IntegrationAuditLog:
    from fiscal_gateway.models import As4DocumentLink, FiscalSubmissionLog
    from invoices.models import Invoice
    from super_integration.models import SuperDocumentLink

    invoice_ref = invoice if isinstance(invoice, Invoice) else None
    super_link_ref = super_link if isinstance(super_link, SuperDocumentLink) else None
    fiscal_log_ref = fiscal_log if isinstance(fiscal_log, FiscalSubmissionLog) else None
    as4_link_ref = as4_link if isinstance(as4_link, As4DocumentLink) else None
    entry = IntegrationAuditLog.all_objects.create(
        tenant=tenant,
        correlation_id=correlation_id,
        invoice=invoice_ref,
        integration_type=integration_type,
        provider=provider,
        step=step,
        status=status,
        detail=detail or {},
        triggered_by=triggered_by,
        super_link=super_link_ref,
        fiscal_log=fiscal_log_ref,
        as4_link=as4_link_ref,
    )
    _maybe_run_alerts(tenant, step, status)
    return entry


def timed_detail(**extra: Any) -> dict[str, Any]:
    return dict(extra)


def measure_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
