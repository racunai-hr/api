from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Domibus outgoing statuses that imply delivery to recipient AP
_DELIVERED_STATUSES = frozenset({
    'ACKNOWLEDGED',
    'MESSAGE_SEND_SUCCESS',
    'SEND_SUCCESS',
    'WAITING_FOR_RECEIPT',
})

_FAILED_STATUSES = frozenset({
    'MESSAGE_SEND_FAILURE',
    'SEND_FAILURE',
    'ERROR',
    'ABORT',
})


class DomibusStatusError(Exception):
    pass


def _rest_base_url(ws_url: str) -> str:
    """Derive Domibus REST base from WS plugin URL."""
    parsed = urlparse(ws_url.rstrip('/'))
    path = parsed.path or '/'
    for suffix in ('/services/wsPlugin', '/services/wsplugin', '/services/wsPlugin/'):
        if path.lower().endswith(suffix.lower().rstrip('/')):
            path = path[: -len(suffix.rstrip('/'))]
            break
    path = path.rstrip('/') + '/rest'
    return f'{parsed.scheme}://{parsed.netloc}{path}'


def _auth_session(rest_base: str) -> requests.Session:
    user = getattr(settings, 'DOMIBUS_ADMIN_USER', 'admin')
    password = getattr(settings, 'DOMIBUS_ADMIN_PASS', '')
    session = requests.Session()
    session.auth = (user, password)
    try:
        resp = session.post(
            urljoin(rest_base + '/', 'security/authentication'),
            json={'username': user, 'password': password},
            timeout=15,
        )
        if resp.status_code >= 400:
            logger.debug('Domibus session auth HTTP %s', resp.status_code)
    except requests.RequestException as exc:
        logger.debug('Domibus session auth failed: %s', exc)
    return session


def fetch_message_status(message_id: str, *, domibus_ws_url: str | None = None) -> str | None:
    """Return raw Domibus message status string, or None if unavailable."""
    ws_url = (domibus_ws_url or getattr(settings, 'DOMIBUS_WS_URL', '') or '').strip()
    if not ws_url or not message_id:
        return None

    rest_base = _rest_base_url(ws_url)
    session = _auth_session(rest_base)

    candidates = [
        f'messages/{message_id}',
        f'ext/messages/usermessages/{message_id}',
    ]
    for path in candidates:
        url = urljoin(rest_base + '/', path)
        try:
            resp = session.get(url, timeout=20)
        except requests.RequestException as exc:
            logger.debug('Domibus status GET %s failed: %s', url, exc)
            continue
        if resp.status_code == 404:
            continue
        if resp.status_code >= 400:
            logger.debug('Domibus status HTTP %s for %s', resp.status_code, message_id)
            continue
        try:
            payload = resp.json()
        except ValueError:
            continue
        status = (
            payload.get('messageStatus')
            or payload.get('status')
            or (payload.get('messageInfo') or {}).get('status')
        )
        if status:
            return str(status).upper()
    return None


def map_domibus_status(raw_status: str) -> str | None:
    """Map Domibus status to As4DocumentLink status constant value."""
    from fiscal_gateway.models import As4DocumentLink

    upper = raw_status.upper()
    if upper in _DELIVERED_STATUSES:
        return As4DocumentLink.STATUS_DELIVERED
    if upper in _FAILED_STATUSES:
        return As4DocumentLink.STATUS_FAILED
    return None
