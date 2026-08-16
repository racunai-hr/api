from __future__ import annotations

import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

import requests
from django.utils import timezone

from banking.provider_models import BankApiCall, BankProvider
from banking.otp.signing import OtpSigningError, apply_api_request_signature, serialize_body
from fiscal_gateway.client.certificate import CertificateError, load_p12


class OtpClientError(Exception):
    pass


@contextmanager
def _mtls_cert_files(cert_path: str, cert_password: str):
    try:
        material = load_p12(cert_path, cert_password)
    except CertificateError as exc:
        raise OtpClientError(str(exc)) from exc

    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = Path(tmpdir) / 'key.pem'
        cert_file = Path(tmpdir) / 'cert.pem'
        key_path.write_bytes(material.private_key_pem)
        cert_file.write_bytes(material.certificate_pem)
        for idx, chain_pem in enumerate(material.chain_pem):
            cert_file.write_bytes(chain_pem)
        yield str(cert_file), str(key_path)


class OtpHttpClient:
    def __init__(
        self,
        *,
        provider: BankProvider,
        credentials,
        tenant=None,
        correlation_id=None,
        timeout: int = 30,
    ):
        self.provider = provider
        self.credentials = credentials
        self.tenant = tenant
        self.correlation_id = correlation_id
        self.timeout = timeout
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is not None:
            return self._session
        session = requests.Session()
        session.verify = True
        self._session = session
        return session

    def _is_api_url(self, url: str) -> bool:
        return url.startswith(self.provider.api_base.rstrip('/'))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        json=None,
        data=None,
        params=None,
        access_token: str | None = None,
    ) -> requests.Response:
        request_id = str(uuid.uuid4())
        req_headers = {
            'X-Request-ID': request_id,
            'Accept': 'application/json',
            **(headers or {}),
        }
        if access_token:
            req_headers['Authorization'] = f'Bearer {access_token}'

        body = serialize_body(json_payload=json, data=data)
        if self._is_api_url(url):
            try:
                req_headers = apply_api_request_signature(req_headers, body)
            except OtpSigningError as exc:
                raise OtpClientError(str(exc)) from exc
            if json is not None:
                req_headers.setdefault('Content-Type', 'application/json')
                json = None
                data = body

        started = perf_counter()
        error_summary = ''
        response = None
        try:
            with _mtls_cert_files(self.credentials.cert_path, self.credentials.cert_password) as (
                cert_file,
                key_file,
            ):
                response = self._get_session().request(
                    method,
                    url,
                    headers=req_headers,
                    json=json,
                    data=data,
                    params=params,
                    cert=(cert_file, key_file),
                    timeout=self.timeout,
                )
            if response.status_code >= 400:
                error_summary = (response.text or '')[:500]
            return response
        except requests.RequestException as exc:
            error_summary = str(exc)[:500]
            raise OtpClientError(str(exc)) from exc
        finally:
            duration_ms = int((perf_counter() - started) * 1000)
            BankApiCall.objects.create(
                tenant=self.tenant,
                provider=self.provider,
                method=method.upper(),
                url=url,
                request_id=request_id,
                http_status=getattr(response, 'status_code', None),
                duration_ms=duration_ms,
                correlation_id=self.correlation_id,
                error_summary=error_summary,
            )

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request('GET', self._url(path), **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request('POST', self._url(path), **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        return self.request('PUT', self._url(path), **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return self.request('DELETE', self._url(path), **kwargs)

    def iam_get(self, path: str, **kwargs) -> requests.Response:
        return self.request('GET', self._iam_url(path), **kwargs)

    def iam_post(self, path: str, **kwargs) -> requests.Response:
        return self.request('POST', self._iam_url(path), **kwargs)

    def _url(self, path: str) -> str:
        base = self.provider.api_base.rstrip('/')
        if path.startswith('http'):
            return path
        return f'{base}/{path.lstrip("/")}'

    def _iam_url(self, path: str) -> str:
        base = self.provider.iam_base.rstrip('/')
        if path.startswith('http'):
            return path
        return f'{base}/{path.lstrip("/")}'

    def ping(self) -> bool:
        try:
            response = self.iam_get('/.well-known/openid-configuration')
            return response.status_code == 200
        except OtpClientError:
            return False

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def build_client(*, provider: BankProvider, tenant=None, correlation_id=None) -> OtpHttpClient:
    from banking.config import resolve_otp_credentials

    return OtpHttpClient(
        provider=provider,
        credentials=resolve_otp_credentials(),
        tenant=tenant,
        correlation_id=correlation_id,
    )
