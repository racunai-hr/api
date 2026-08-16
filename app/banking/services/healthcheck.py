from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from banking.config import get_active_otp_provider, resolve_otp_credentials
from banking.provider_models import BankConnection, BankProvider
from fiscal_gateway.client.certificate import CertificateError, load_p12


@dataclass
class HealthcheckItem:
    label: str
    passed: bool
    detail: str = ''


@dataclass
class HealthcheckReport:
    checks: list[HealthcheckItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, label: str, passed: bool, detail: str = '') -> None:
        self.checks.append(HealthcheckItem(label=label, passed=passed, detail=detail))


def run_otp_healthcheck(*, provider: str = 'otp_sandbox') -> HealthcheckReport:
    """Provjera statičke OTP konfiguracije (cert, env, provider) bez mrežnih poziva."""
    report = HealthcheckReport()
    credentials = resolve_otp_credentials()
    cert_path = Path(credentials.cert_path)

    if cert_path.is_file():
        report.add('P12 loaded', True)
        try:
            material = load_p12(cert_path, credentials.cert_password)
            days_left = (material.not_valid_after - timezone.now()).days
            report.add(
                f'Certificate valid, expires in {days_left} days',
                True,
            )
            thumbprint = hashlib.sha256(material.certificate_pem).hexdigest()
            report.add(f'Certificate thumbprint: sha256:{thumbprint}', True)
        except CertificateError as exc:
            report.add(str(exc), False)
    else:
        report.add(f'P12 not found: {cert_path}', False)

    report.add('client_id configured', bool(credentials.client_id))
    report.add('client_secret configured', bool(credentials.client_secret))

    bank_provider = BankProvider.objects.filter(code=provider, is_active=True).first()
    if bank_provider is None:
        report.add(f'provider missing ({provider})', False)
    else:
        report.add(f'provider exists ({provider})', True)
        if credentials.redirect_uri:
            report.add(f'redirect URI configured ({credentials.redirect_uri})', True)
        else:
            report.add('redirect URI missing', False)

    env = getattr(settings, 'OTP_ENV', 'sandbox')
    report.add(f'env OTP_ENV={env}', True)

    return report


def otp_healthcheck_provider_mismatch(*, provider: str) -> str | None:
    """Upozorenje ako aktivni provider ne odgovara traženom kodu."""
    if not provider.startswith('otp'):
        return None
    active = get_active_otp_provider()
    if active and active.code != provider:
        return f'Active OTP provider is {active.code}, not {provider}'
    return None


def run_pis_healthcheck(
    *,
    connection_id: int | None = None,
    tenant: str | None = None,
    min_sync_streak: int = 3,
) -> HealthcheckReport:
    """Provjera PIS preduvjeta (cert, connected veza, consent, sync streak)."""
    report = HealthcheckReport()
    credentials = resolve_otp_credentials()
    cert_path = Path(credentials.cert_path)

    if cert_path.is_file():
        try:
            material = load_p12(cert_path, credentials.cert_password)
            days_left = (material.not_valid_after - timezone.now()).days
            report.add(f'P12 valjan ({days_left} dana)', True)
        except CertificateError as exc:
            report.add(str(exc), False)
    else:
        report.add(f'P12 nije pronađen: {cert_path}', False)

    provider = get_active_otp_provider()
    if provider is None:
        report.add('OTP provider nije konfiguriran.', False)
    else:
        report.add(f'provider {provider.code}', True)

    connection = _resolve_pis_connection(connection_id=connection_id, tenant=tenant)
    if connection is None:
        report.add('Nema connected bankovne veze.', False)
    else:
        report.add(
            f'connection #{connection.pk} ({connection.tenant.slug})',
            True,
        )
        consent = connection.get_active_consent()
        if consent is None:
            report.add('Nema aktivnog consenta.', False)
        else:
            report.add(
                f'consent {consent.consent_id or consent.pk} ({consent.status})',
                True,
            )

        if connection.bank_account.iban:
            report.add(f'debtor IBAN {connection.bank_account.iban}', True)
        else:
            report.add('Debtor IBAN nije postavljen.', False)

        if connection.is_pis_ready(min_streak=min_sync_streak):
            report.add(
                f'PIS gate OK (sync streak {connection.sync_success_streak})',
                True,
            )
        else:
            report.add(
                f'PIS gate: streak {connection.sync_success_streak} < {min_sync_streak}',
                False,
            )

    return report


def _resolve_pis_connection(
    *,
    connection_id: int | None,
    tenant: str | None,
) -> BankConnection | None:
    qs = BankConnection.all_objects.select_related('bank_account', 'tenant')
    if connection_id:
        return qs.filter(pk=connection_id, status='connected').first()
    if tenant:
        return (
            qs.filter(tenant__slug=tenant, status='connected')
            .order_by('-updated_at')
            .first()
        )
    return qs.filter(status='connected').order_by('-updated_at').first()
