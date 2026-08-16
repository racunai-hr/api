from __future__ import annotations

from urllib.parse import urlencode

from banking.otp.client import OtpClientError


class OauthStateError(Exception):
    pass


def sign_oauth_state(payload: dict) -> str:
    import base64
    import hashlib
    import hmac
    import json

    from django.conf import settings

    body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(body).decode().rstrip('=')
    signature = hmac.new(
        settings.SECRET_KEY.encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f'{encoded}.{signature}'


def verify_oauth_state(state: str) -> dict:
    import base64
    import hashlib
    import hmac
    import json
    import time

    from django.conf import settings

    try:
        encoded, signature = state.rsplit('.', 1)
    except ValueError as exc:
        raise OauthStateError('Neispravan state format.') from exc

    expected = hmac.new(
        settings.SECRET_KEY.encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise OauthStateError('Neispravan potpis state parametra.')

    padding = '=' * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
    exp = payload.get('exp')
    if exp is not None and time.time() > float(exp):
        raise OauthStateError('State je istekao.')
    return payload


def build_authorize_url(
    *,
    iam_base: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scope: str = 'OTP.PSD2',
) -> str:
    params = {
        'response_type': 'code',
        'grant_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': scope,
        'state': state,
    }
    base = iam_base.rstrip('/')
    return f'{base}/connect/authorize?{urlencode(params)}'


def exchange_code_for_token(client, code: str, redirect_uri: str, *, scope: str = 'OTP.PSD2') -> dict:
    response = client.request(
        'POST',
        client._iam_url('/connect/token'),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': client.credentials.client_id,
            'client_secret': client.credentials.client_secret,
            'scope': scope,
        },
    )
    if response.status_code >= 400:
        raise OtpClientError(f'Token exchange failed: {response.status_code} {response.text[:200]}')
    return response.json()
