"""HTTP client for the Fiskal Platform REST API."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings

from fiscal_gateway.config_utils import get_active_fiscal_config


class FiscalPlatformError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class FiscalPlatformTimeout(FiscalPlatformError):
    pass


@dataclass(frozen=True)
class FiscalPlatformConfig:
    tenant: Any
    organization_oib: str
    fiscal_profile_slug: str
    api_base_url: str
    api_token: str
    poll_timeout_seconds: int = 120
    poll_interval_seconds: float = 2.0

    @classmethod
    def from_tenant(cls, tenant) -> FiscalPlatformConfig:
        fiscal_config = get_active_fiscal_config(tenant)
        base_url = (getattr(settings, 'FISKAL_PLATFORM_URL', '') or '').rstrip('/')
        token = (getattr(settings, 'FISKAL_PLATFORM_API_TOKEN', '') or '').strip()
        if not base_url:
            raise FiscalPlatformError('FISKAL_PLATFORM_URL is not configured')
        if not token:
            raise FiscalPlatformError('FISKAL_PLATFORM_API_TOKEN is not configured')

        return cls(
            tenant=tenant,
            organization_oib=fiscal_config.oib,
            fiscal_profile_slug=getattr(settings, 'FISKAL_PLATFORM_PROFILE_SLUG', 'finestar'),
            api_base_url=base_url,
            api_token=token,
            poll_timeout_seconds=int(
                getattr(settings, 'FISKAL_PLATFORM_POLL_TIMEOUT_SECONDS', 120)
            ),
            poll_interval_seconds=float(
                getattr(settings, 'FISKAL_PLATFORM_POLL_INTERVAL_SECONDS', 2.0)
            ),
        )


class FiscalPlatformClient:
    TERMINAL_STATUSES = frozenset({'accepted', 'failed', 'dead'})

    def __init__(self, config: FiscalPlatformConfig):
        self.config = config
        self._session = requests.Session()
        self._session.headers.update(
            {
                'Authorization': f'Bearer {config.api_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-Organization-OIB': config.organization_oib,
            }
        )

    def submit_fiscalization(
        self,
        *,
        payload: dict,
        correlation_id: str | None = None,
        external_reference: str | None = None,
        idempotency_key: str | None = None,
        fiscal_mode: str = 'f2',
        source_application: str = 'racunai',
    ) -> dict[str, Any]:
        body = {
            'document_type': 'invoice',
            'schema_name': 'invoice-v1',
            'schema_version': '1.0',
            'fiscal_mode': fiscal_mode,
            'source_application': source_application,
            'fiscal_profile_slug': self.config.fiscal_profile_slug,
            'organization_oib': self.config.organization_oib,
            'payload': payload,
        }
        if external_reference:
            body['external_reference'] = external_reference

        headers: dict[str, str] = {}
        if correlation_id:
            headers['X-Correlation-ID'] = correlation_id
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key

        url = f'{self.config.api_base_url}/api/v1/fiscalizations'
        response = self._session.post(url, json=body, headers=headers, timeout=60)
        if response.status_code not in (200, 202):
            self._raise_for_response(response)
        return response.json()

    def get_fiscalization(self, request_id: str) -> dict[str, Any]:
        url = f'{self.config.api_base_url}/api/v1/fiscalizations/{request_id}'
        response = self._session.get(url, timeout=30)
        if response.status_code != 200:
            self._raise_for_response(response)
        return response.json()

    def submit_and_wait(
        self,
        *,
        payload: dict,
        correlation_id: str | None = None,
        external_reference: str | None = None,
        idempotency_key: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if dry_run:
            return {
                'status': 'accepted',
                'jir': 'DRY-RUN-JIR',
                'zki': 'DRY-RUN-ZKI',
                'dry_run': True,
            }

        accepted = self.submit_fiscalization(
            payload=payload,
            correlation_id=correlation_id,
            external_reference=external_reference,
            idempotency_key=idempotency_key,
        )
        request_id = accepted['request_id']
        return self.wait_for_completion(request_id)

    def wait_for_completion(self, request_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.poll_timeout_seconds
        while time.monotonic() < deadline:
            detail = self.get_fiscalization(request_id)
            status = (detail.get('status') or '').lower()
            if status in self.TERMINAL_STATUSES:
                detail['request_id'] = request_id
                return detail
            time.sleep(self.config.poll_interval_seconds)
        raise FiscalPlatformTimeout(
            f'Fiscal platform request {request_id} did not reach a terminal status '
            f'within {self.config.poll_timeout_seconds}s'
        )

    @staticmethod
    def _raise_for_response(response: requests.Response) -> None:
        payload: dict[str, Any]
        try:
            payload = response.json()
        except ValueError:
            payload = {'message': response.text}
        message = payload.get('message') or response.reason or 'Fiskal platform request failed'
        raise FiscalPlatformError(message, status_code=response.status_code, payload=payload)
