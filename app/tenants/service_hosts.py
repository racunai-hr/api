from __future__ import annotations

from enum import Enum

from django.conf import settings

from .resolution import normalize_host


class ServiceHostKind(Enum):
    TENANT = 'tenant'
    ADMIN = 'admin'
    MPS = 'mps'
    OTP = 'otp'
    AS4 = 'as4'


def _build_service_hosts() -> dict[str, ServiceHostKind]:
    hosts: dict[str, ServiceHostKind] = {}
    for host in getattr(settings, 'TENANT_PLATFORM_ADMIN_HOSTS', []):
        hosts[normalize_host(host)] = ServiceHostKind.ADMIN

    mps_url = getattr(settings, 'MPS_SERVICE_URL', '')
    if mps_url:
        from urllib.parse import urlparse

        mps_host = urlparse(mps_url).hostname
        if mps_host:
            hosts[normalize_host(mps_host)] = ServiceHostKind.MPS

    otp_host = getattr(settings, 'OTP_SERVICE_HOST', '')
    if otp_host:
        hosts[normalize_host(otp_host)] = ServiceHostKind.OTP

    for host in ('otp-sbx.racunai.hr', 'otp.racunai.hr', 'mps.racunai.hr'):
        hosts.setdefault(normalize_host(host), ServiceHostKind.OTP if 'otp' in host else ServiceHostKind.MPS)

    as4_hosts = getattr(settings, 'FISCAL_AS4_SERVICE_HOSTS', [])
    for host in as4_hosts:
        hosts[normalize_host(host)] = ServiceHostKind.AS4

    return hosts


def resolve_service_host(host: str) -> ServiceHostKind | None:
    normalized = normalize_host(host)
    return _build_service_hosts().get(normalized)


def is_otp_service_host(host: str) -> bool:
    return resolve_service_host(host) == ServiceHostKind.OTP


def is_as4_inbound_path(path: str) -> bool:
    return path.startswith('/api/fiscal/as4/inbound')
