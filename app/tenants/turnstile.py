import json
import urllib.error
import urllib.parse
import urllib.request


def turnstile_required_for_request(request) -> bool:
    from django.conf import settings

    if not getattr(settings, 'TURNSTILE_VERIFY_ENABLED', False):
        return False
    if not getattr(settings, 'TURNSTILE_SITE_KEY', ''):
        return False

    host = request.get_host().split(':')[0].lower()
    allowed_hosts = getattr(settings, 'TURNSTILE_ADMIN_HOSTS', ['admin.racunai.hr'])
    return host in {h.lower() for h in allowed_hosts}


def verify_turnstile(token: str, remote_ip: str | None = None) -> bool:
    from django.conf import settings

    if not getattr(settings, 'TURNSTILE_VERIFY_ENABLED', False):
        return True

    secret = getattr(settings, 'TURNSTILE_SECRET_KEY', '')
    if not secret or not token:
        return False

    payload = {'secret': secret, 'response': token}
    if remote_ip:
        payload['remoteip'] = remote_ip

    data = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(
        'https://challenges.cloudflare.com/turnstile/v0/siteverify',
        data=data,
        method='POST',
    )

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False

    return result.get('success') is True
