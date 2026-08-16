from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from banking.config import get_active_otp_provider, resolve_otp_credentials
from banking.otp.client import build_client
from banking.provider_models import BankConnection, PaymentOrder
from banking.services.pis_orders import (
    create_draft_payment_order,
    resolve_pending_payment_order,
    submit_domestic_payment_order,
)
from fiscal_gateway.client.certificate import CertificateError, load_p12
from pathlib import Path
import hashlib
from django.utils import timezone


@dataclass
class PisE2eCheck:
    name: str
    passed: bool
    detail: str = ''


@dataclass
class PisE2eReport:
    checks: list[PisE2eCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def add(self, name: str, passed: bool, detail: str = '') -> None:
        self.checks.append(PisE2eCheck(name=name, passed=passed, detail=detail))


def run_pis_e2e_checks(
    connection: BankConnection,
    *,
    submit_test_payment: bool = False,
    min_sync_streak: int = 3,
) -> PisE2eReport:
    report = PisE2eReport()
    credentials = resolve_otp_credentials()

    cert_path = Path(credentials.cert_path)
    if cert_path.is_file():
        try:
            material = load_p12(cert_path, credentials.cert_password)
            days_left = (material.not_valid_after - timezone.now()).days
            report.add('pis_healthcheck_p12', True, f'valjan {days_left}d')
        except CertificateError as exc:
            report.add('pis_healthcheck_p12', False, str(exc))
    else:
        report.add('pis_healthcheck_p12', False, f'nedostaje {cert_path}')

    report.add(
        'bank_connection_connected',
        connection.status == 'connected',
        f'status={connection.status}',
    )
    consent = connection.get_active_consent()
    if consent is None:
        report.add('active_consent', False, 'nema authorized consenta')
        return report

    report.add('consent_authorized', consent.status in ('authorized', 'valid'), consent.status)
    report.add(
        'pis_ready_gate',
        connection.is_pis_ready(min_streak=min_sync_streak),
        f'sync_success_streak={connection.sync_success_streak} (min {min_sync_streak})',
    )
    report.add(
        'debtor_iban_configured',
        bool(connection.bank_account.iban),
        connection.bank_account.iban or '(prazno)',
    )

    if submit_test_payment:
        _check_test_payment_submission(report, connection, min_sync_streak=min_sync_streak)
    else:
        pending = resolve_pending_payment_order()
        report.add(
            'no_stale_sca_payment',
            pending is None,
            f'pending_order={pending.pk if pending else "none"}',
        )

    return report


def _check_test_payment_submission(
    report: PisE2eReport,
    connection: BankConnection,
    *,
    min_sync_streak: int,
) -> None:
    try:
        order = create_draft_payment_order(
            connection,
            creditor_iban='HR0924070000000001401',
            creditor_name='PIS E2E Test',
            amount=Decimal('1.00'),
            currency='EUR',
            reference='PIS-E2E-TEST',
        )
        submit_domestic_payment_order(order, min_sync_streak=min_sync_streak)
        report.add(
            'payment_initiated',
            bool(order.otp_payment_id),
            f'paymentId={order.otp_payment_id or "(prazno)"}, status={order.status}',
        )
        report.add(
            'sca_redirect_or_submitted',
            bool(order.sca_redirect_url) or order.status in ('submitted', 'sca_required'),
            order.sca_redirect_url[:80] if order.sca_redirect_url else order.status,
        )
    except Exception as exc:
        report.add('payment_initiated', False, str(exc))


def resolve_pis_e2e_connection(
    *,
    connection_id: int | None = None,
    tenant_slug: str | None = None,
) -> BankConnection:
    qs = BankConnection.all_objects.select_related('bank_provider', 'bank_account', 'tenant')
    if connection_id is not None:
        return qs.get(pk=connection_id)
    if tenant_slug:
        connection = (
            qs.filter(tenant__slug=tenant_slug, status='connected')
            .order_by('-updated_at')
            .first()
        )
        if connection is None:
            raise BankConnection.DoesNotExist(f'Nema connected veze za {tenant_slug}.')
        return connection

    provider = get_active_otp_provider()
    if provider is None:
        raise BankConnection.DoesNotExist('Nema aktivnog OTP providera.')
    connection = (
        qs.filter(bank_provider=provider, status='connected')
        .order_by('-updated_at')
        .first()
    )
    if connection is None:
        raise BankConnection.DoesNotExist('Nema connected OTP veze.')
    return connection
