from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings


class MpsClientError(Exception):
    pass


@dataclass(frozen=True)
class MpsHealth:
    status: str
    detail: str = ''


class MpsClient:
    """HTTP wrapper for the racunAI MPS microservice (FastAPI at services/mps/)."""

    def __init__(self, base_url: str | None = None, *, timeout: int = 60):
        self.base_url = (base_url or getattr(settings, 'MPS_SERVICE_URL', '')).rstrip('/')
        self.timeout = timeout

    def health(self) -> MpsHealth:
        response = self._request('GET', '/health')
        data = response.json()
        return MpsHealth(status=data.get('status', 'unknown'), detail=str(data))

    def ams_list(self) -> dict:
        response = self._request('GET', '/admin/ams/list')
        return response.json()

    def ams_create(self, oib: str) -> dict:
        response = self._request('POST', '/admin/ams/create', json={'oib': oib})
        return response.json()

    def ams_delete(self, oib: str) -> dict:
        response = self._request('POST', '/admin/ams/delete', json={'oib': oib})
        return response.json()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        if not self.base_url:
            raise MpsClientError('MPS_SERVICE_URL nije postavljen')
        url = f'{self.base_url}{path}'
        try:
            response = requests.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise MpsClientError(f'MPS servis nedostupan ({url}): {exc}') from exc
        if response.status_code >= 400:
            detail = response.text[:500]
            try:
                detail = response.json().get('detail', detail)
            except ValueError:
                pass
            raise MpsClientError(f'MPS {method} {path} HTTP {response.status_code}: {detail}')
        return response
