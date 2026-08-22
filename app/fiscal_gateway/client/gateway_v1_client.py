"""Thin JWT client for intermediary Fiscal Gateway /v1 (eRačun)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import requests
from django.conf import settings


class GatewayV1Error(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        payload: dict | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}


class GatewayV1Client:
    """Read/write client for MPS gateway `/v1`. Never talks to Super/Domibus."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: int = 30,
        taxpayer_oib: str | None = None,
    ):
        self.base_url = (base_url or getattr(settings, 'MPS_SERVICE_URL', '') or '').rstrip('/')
        self.timeout = timeout
        self.taxpayer_oib = taxpayer_oib

    def get_inbound_document(self, document_id: str) -> dict:
        return self._request('GET', f'/v1/inbound/documents/{document_id}').json()

    def list_inbound_documents(
        self,
        taxpayer_oib: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict:
        params: dict[str, Any] = {'limit': limit}
        if cursor:
            params['cursor'] = cursor
        return self._request(
            'GET',
            f'/v1/taxpayers/{taxpayer_oib}/inbound/documents',
            params=params,
        ).json()

    def provider_capabilities(self, provider: str, *, taxpayer_oib: str | None = None) -> dict:
        params = {}
        if taxpayer_oib:
            params['taxpayer_oib'] = taxpayer_oib
        return self._request(
            'GET',
            f'/v1/providers/{provider}/capabilities',
            params=params or None,
        ).json()

    def reject_e_reporting(
        self,
        document_id: str,
        *,
        reason_code: str,
        reason_text: str = '',
        idempotency_key: str,
    ) -> tuple[int, dict]:
        response = self._request(
            'POST',
            f'/v1/inbound/documents/{document_id}/e-reporting/rejection',
            json={'reason_code': reason_code, 'reason_text': reason_text or ''},
            headers={'Idempotency-Key': idempotency_key},
            scope='gateway.write',
            raise_for_status=False,
        )
        try:
            body = response.json()
        except ValueError:
            body = {'detail': response.text[:500]}
        if response.status_code >= 400:
            code = None
            if isinstance(body, dict):
                err = body.get('error') if isinstance(body.get('error'), dict) else body
                if isinstance(err, dict):
                    code = err.get('code') or err.get('error_code')
            raise GatewayV1Error(
                f'Gateway reject HTTP {response.status_code}',
                status_code=response.status_code,
                code=code,
                payload=body if isinstance(body, dict) else {},
            )
        return response.status_code, body if isinstance(body, dict) else {}

    def _mint_token(self, *, scope: str) -> str:
        secret = (getattr(settings, 'GATEWAY_JWT_SECRET', '') or '').strip()
        if not secret:
            raise GatewayV1Error('GATEWAY_JWT_SECRET is not configured')
        iss = getattr(settings, 'GATEWAY_JWT_ISS', 'racunai-api')
        aud = getattr(settings, 'GATEWAY_JWT_AUD', 'racunai-intermediary')
        now = datetime.now(timezone.utc)
        taxpayers = [self.taxpayer_oib] if self.taxpayer_oib else ['*']
        payload = {
            'iss': iss,
            'aud': aud,
            'exp': now + timedelta(minutes=5),
            'jti': str(uuid.uuid4()),
            'sub': 'racunai-api',
            'scope': scope,
            'taxpayers': taxpayers,
        }
        return jwt.encode(payload, secret, algorithm='HS256')

    def _request(
        self,
        method: str,
        path: str,
        *,
        scope: str = 'gateway.read',
        raise_for_status: bool = True,
        headers: dict | None = None,
        **kwargs,
    ) -> requests.Response:
        if not self.base_url:
            raise GatewayV1Error('MPS_SERVICE_URL is not configured')
        url = f'{self.base_url}{path}'
        req_headers = {
            'Authorization': f'Bearer {self._mint_token(scope=scope)}',
            'Accept': 'application/json',
        }
        if headers:
            req_headers.update(headers)
        try:
            response = requests.request(
                method,
                url,
                timeout=self.timeout,
                headers=req_headers,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise GatewayV1Error(f'Gateway unavailable ({url}): {exc}') from exc
        if raise_for_status and response.status_code >= 400:
            detail = response.text[:500]
            code = None
            try:
                data = response.json()
                if isinstance(data, dict):
                    err = data.get('error') if isinstance(data.get('error'), dict) else data
                    if isinstance(err, dict):
                        detail = str(err.get('detail') or err.get('message') or detail)
                        code = err.get('code') or err.get('error_code')
            except ValueError:
                pass
            raise GatewayV1Error(
                f'Gateway {method} {path} HTTP {response.status_code}: {detail}',
                status_code=response.status_code,
                code=code,
            )
        return response
