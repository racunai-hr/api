from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from django.db.models import Avg, Count, Q
from django.utils import timezone

from integrations.models import IntegrationAuditLog


def _since(days: int):
    return timezone.now() - timedelta(days=days)


def _base_qs(tenant=None, *, days: int = 7):
    qs = IntegrationAuditLog.all_objects.filter(created_at__gte=_since(days))
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
    return qs


def outbound_summary(*, tenant=None, days: int = 7) -> list[dict[str, Any]]:
    qs = _base_qs(tenant, days=days).filter(
        step__in=(
            IntegrationAuditLog.STEP_OUTBOUND_SENT,
            IntegrationAuditLog.STEP_OUTBOUND_FAILED,
        ),
    )
    rows = (
        qs.values('provider')
        .annotate(
            sent=Count('id', filter=Q(step=IntegrationAuditLog.STEP_OUTBOUND_SENT)),
            failed=Count('id', filter=Q(step=IntegrationAuditLog.STEP_OUTBOUND_FAILED)),
        )
        .order_by('provider')
    )
    return [dict(row) for row in rows]


def errors_by_step(*, tenant=None, days: int = 7) -> list[dict[str, Any]]:
    qs = _base_qs(tenant, days=days).filter(status=IntegrationAuditLog.STATUS_FAILED)
    rows = qs.values('step').annotate(count=Count('id')).order_by('-count')
    return [dict(row) for row in rows]


def latency_percentiles(*, tenant=None, days: int = 7) -> dict[str, int | None]:
    qs = _base_qs(tenant, days=days)
    durations: list[int] = []
    for entry in qs.values_list('detail', flat=True):
        if isinstance(entry, dict):
            ms = entry.get('duration_ms')
            if ms is not None:
                durations.append(int(ms))

    if not durations:
        return {'p50': None, 'p95': None, 'avg': None}

    durations.sort()
    n = len(durations)
    p50 = durations[n // 2]
    p95 = durations[int(n * 0.95)] if n > 1 else durations[-1]
    avg = int(sum(durations) / n)
    return {'p50': p50, 'p95': p95, 'avg': avg}


def schematron_skip_count(*, tenant=None, days: int = 7) -> int:
    return _base_qs(tenant, days=days).filter(
        step=IntegrationAuditLog.STEP_SCHEMATRON_SKIPPED,
    ).count()


def document_direction_counts(*, tenant=None, days: int = 7) -> dict[str, int]:
    qs = _base_qs(tenant, days=days)
    return {
        'outbound_sent': qs.filter(step=IntegrationAuditLog.STEP_OUTBOUND_SENT).count(),
        'inbound_as4': qs.filter(as4_link__isnull=False, as4_link__direction='inbound').count(),
        'as4_outbound': qs.filter(as4_link__isnull=False, as4_link__direction='outbound').count(),
        'super_outbound': qs.filter(super_link__isnull=False, super_link__direction='outbound').count(),
    }


def correlation_timeline(correlation_id: uuid.UUID) -> list[IntegrationAuditLog]:
    return list(
        IntegrationAuditLog.all_objects.filter(correlation_id=correlation_id).order_by('created_at')
    )


def ops_dashboard_context(*, tenant=None, days: int = 7) -> dict[str, Any]:
    return {
        'days': days,
        'tenant': tenant,
        'outbound': outbound_summary(tenant=tenant, days=days),
        'errors_by_step': errors_by_step(tenant=tenant, days=days),
        'latency': latency_percentiles(tenant=tenant, days=days),
        'schematron_skipped': schematron_skip_count(tenant=tenant, days=days),
        'directions': document_direction_counts(tenant=tenant, days=days),
    }
