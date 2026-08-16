from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import mail_admins
from django.db.models import Count, Q
from django.utils import timezone

from integrations.constants import IntegrationEnvironment
from integrations.models import IntegrationAuditLog, IntegrationConfig

logger = logging.getLogger(__name__)


def _window_start(hours: int | None = None):
    hours = hours or getattr(settings, 'INTEGRATION_ALERT_WINDOW_HOURS', 1)
    return timezone.now() - timedelta(hours=hours)


def check_outbound_failed_alert(*, tenant=None) -> bool:
    threshold = getattr(settings, 'INTEGRATION_ALERT_OUTBOUND_FAILED_THRESHOLD', 5)
    qs = IntegrationAuditLog.all_objects.filter(
        step=IntegrationAuditLog.STEP_OUTBOUND_FAILED,
        status=IntegrationAuditLog.STATUS_FAILED,
        created_at__gte=_window_start(),
    )
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
    count = qs.count()
    if count < threshold:
        return False

    subject = f'[racunAI] outbound_failed alert: {count} u zadnjem satu'
    body = (
        f'Detektirano {count} outbound_failed događaja (prag: {threshold}).\n'
        f'Tenant: {tenant.slug if tenant else "svi"}\n'
    )
    _notify(subject, body, tenant=tenant, title='Outbound neuspjesi', priority='urgent')
    return True


def check_fiscal_failed_alert(*, tenant=None) -> bool:
    qs = IntegrationAuditLog.all_objects.filter(
        step=IntegrationAuditLog.STEP_FISCAL_FAILED,
        status=IntegrationAuditLog.STATUS_FAILED,
        created_at__gte=_window_start(hours=24),
    )
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
        if not IntegrationConfig.all_objects.filter(
            tenant=tenant,
            integration_type='fiscalization',
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        ).exists():
            return False
    else:
        prod_tenant_ids = IntegrationConfig.all_objects.filter(
            integration_type='fiscalization',
            environment=IntegrationEnvironment.PRODUCTION,
            is_active=True,
        ).values_list('tenant_id', flat=True)
        qs = qs.filter(tenant_id__in=prod_tenant_ids)

    count = qs.count()
    if count == 0:
        return False

    subject = f'[racunAI] fiscal_failed alert: {count} u 24h'
    body = f'Detektirano {count} fiscal_failed događaja za produkcijski tenant.\n'
    _notify(subject, body, tenant=tenant, title='Fiskalizacija neuspješna', priority='high')
    return True


def check_schematron_skip_alert(*, tenant=None, days: int = 7) -> bool:
    since = timezone.now() - timedelta(days=days)
    qs = IntegrationAuditLog.all_objects.filter(created_at__gte=since)
    if tenant is not None:
        qs = qs.filter(tenant=tenant)

    totals = qs.aggregate(
        built=Count('id', filter=Q(step=IntegrationAuditLog.STEP_DOCUMENT_BUILT)),
        skipped=Count('id', filter=Q(step=IntegrationAuditLog.STEP_SCHEMATRON_SKIPPED)),
    )
    built = totals['built'] or 0
    skipped = totals['skipped'] or 0
    if built == 0 or skipped < built:
        return False

    subject = '[racunAI] schematron skip rate 100%'
    body = (
        f'Svi dokumenti ({built}) imaju schematron_skipped u zadnjih {days} dana.\n'
        'Provjerite ubl/schematron/ — PU ZIP vjerojatno nedostaje.\n'
    )
    _notify(subject, body, tenant=tenant, title='Schematron preskočen', priority='medium')
    return True


def run_integration_alerts(*, tenant=None) -> dict[str, bool]:
    return {
        'outbound_failed': check_outbound_failed_alert(tenant=tenant),
        'fiscal_failed': check_fiscal_failed_alert(tenant=tenant),
        'schematron_skip': check_schematron_skip_alert(tenant=tenant),
    }


def _notify(subject: str, body: str, *, tenant=None, title: str, priority: str) -> None:
    try:
        mail_admins(subject, body, fail_silently=True)
    except Exception as exc:
        logger.warning('mail_admins failed: %s', exc)

    try:
        from dashboard.models import Notification

        Notification.all_objects.create(
            tenant=tenant,
            title=title,
            message=body,
            priority=priority,
        )
    except Exception as exc:
        logger.debug('Notification create failed: %s', exc)
