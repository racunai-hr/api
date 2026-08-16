from __future__ import annotations

import logging

from django.core.mail import mail_admins
from django.utils import timezone

logger = logging.getLogger(__name__)


def notify_banking_admins(subject: str, body: str) -> None:
    logger.warning('%s\n%s', subject, body)
    try:
        mail_admins(subject, body, fail_silently=True)
    except Exception as exc:
        logger.warning('mail_admins failed: %s', exc)


def notify_consent_expiry(*, tenant_slug: str, consent_id: str, level: str, valid_until) -> None:
    subject = f'[racunAI] OTP consent {level}: {tenant_slug}'
    body = (
        f'Tenant: {tenant_slug}\n'
        f'Consent: {consent_id}\n'
        f'Razina upozorenja: {level}\n'
        f'Vrijedi do: {valid_until}\n'
        f'Vrijeme: {timezone.now():isoformat()}\n'
    )
    notify_banking_admins(subject, body)


def notify_stale_connection(*, connection_id: int, tenant_slug: str, last_sync_at) -> None:
    subject = f'[racunAI] Stale bank connection: {tenant_slug}'
    body = (
        f'Connection #{connection_id} ({tenant_slug}) nema sync >24h.\n'
        f'Zadnji sync: {last_sync_at}\n'
        f'Vrijeme: {timezone.now():isoformat()}\n'
    )
    notify_banking_admins(subject, body)
