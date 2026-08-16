from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

from banking.config import get_active_otp_provider, resolve_otp_credentials
from banking.models import BankStatement, BankTransaction
from banking.otp.ais import fetch_transactions, get_consent_status, list_accounts
from banking.otp.client import OtpClientError, _mtls_cert_files, build_client
from banking.otp.headers import berlin_group_headers
from banking.otp.oauth import build_authorize_url, sign_oauth_state
from banking.provider_models import BankConnection, BankConsent
from banking.services.sync import sync_connection_transactions
from fiscal_gateway.client.certificate import CertificateError, load_p12


@dataclass
class AisE2eCheck:
    name: str
    passed: bool
    detail: str = ''


@dataclass
class AisE2eReport:
    checks: list[AisE2eCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def add(self, name: str, passed: bool, detail: str = '') -> None:
        self.checks.append(AisE2eCheck(name=name, passed=passed, detail=detail))


def run_ais_e2e_checks(
    connection: BankConnection,
    *,
    run_sync: bool = False,
) -> AisE2eReport:
    """Provjeri AIS integraciju nakon uspješnog connect flowa.

    Ne ovisi o broju sandbox transakcija — fokus je na HTTP statusima,
    lifecycle statusima i idempotentnosti synca.
    """
    report = AisE2eReport()
    provider = connection.bank_provider
    credentials = resolve_otp_credentials()

    _check_static_config(report, credentials)
    _check_oauth_authorize_redirect(report, provider, credentials)

    consent = connection.get_active_consent()
    if consent is None:
        report.add('active_consent', False, 'Nema authorized consenta.')
        return report

    _check_consent_scope(report, consent)
    _check_live_ais(report, connection, consent)

    if run_sync:
        _check_sync_pipeline(report, connection)
        _check_connection_state(report, connection)
    else:
        _check_sync_state_without_run(report, connection)
        _check_connection_state(report, connection)

    return report


def _check_static_config(report: AisE2eReport, credentials) -> None:
    cert_path = Path(credentials.cert_path)
    if not cert_path.is_file():
        report.add('healthcheck_p12', False, f'P12 nije pronađen: {cert_path}')
        return

    try:
        material = load_p12(cert_path, credentials.cert_password)
        days_left = (material.not_valid_after - timezone.now()).days
        thumbprint = hashlib.sha256(material.certificate_pem).hexdigest()[:16]
        report.add('healthcheck_p12', True, f'valjan {days_left}d, sha256:{thumbprint}…')
    except CertificateError as exc:
        report.add('healthcheck_p12', False, str(exc))
        return

    report.add(
        'healthcheck_credentials',
        bool(credentials.client_id and credentials.client_secret),
        f'client_id={credentials.client_id or "(prazno)"}',
    )
    report.add(
        'healthcheck_redirect_uri',
        bool(credentials.redirect_uri),
        credentials.redirect_uri or '(prazno)',
    )


def _check_oauth_authorize_redirect(report: AisE2eReport, provider, credentials) -> None:
    import requests

    state = sign_oauth_state({
        'tenant_slug': 'e2e-check',
        'connection_id': 0,
        'nonce': str(uuid.uuid4()),
        'exp': time.time() + 300,
    })
    url = build_authorize_url(
        iam_base=provider.iam_base,
        client_id=credentials.client_id,
        redirect_uri=credentials.redirect_uri,
        state=state,
    )
    try:
        with _mtls_cert_files(credentials.cert_path, credentials.cert_password) as (
            cert_file,
            key_file,
        ):
            response = requests.get(
                url,
                cert=(cert_file, key_file),
                allow_redirects=False,
                timeout=30,
            )
        passed = response.status_code in (302, 303, 307)
        location = response.headers.get('Location', '')[:120]
        report.add(
            'oauth_authorize_redirect',
            passed,
            f'HTTP {response.status_code}' + (f' → {location}' if location else ''),
        )
    except (OtpClientError, requests.RequestException) as exc:
        report.add('oauth_authorize_redirect', False, str(exc))


def _check_connection_state(report: AisE2eReport, connection: BankConnection) -> None:
    report.add(
        'bank_connection_connected',
        connection.status == 'connected',
        f'status={connection.status}',
    )
    report.add(
        'bank_account_external_id',
        bool(connection.bank_account.external_account_id),
        connection.bank_account.external_account_id or '(prazno)',
    )


def _check_consent_scope(report: AisE2eReport, consent: BankConsent) -> None:
    report.add(
        'consent_authorized',
        consent.status in ('authorized', 'valid'),
        f'status={consent.status}, id={consent.consent_id or consent.pk}',
    )
    report.add(
        'consent_access_token',
        bool(consent.access_token),
        f'token_len={len(consent.access_token)}',
    )


def _check_live_ais(
    report: AisE2eReport,
    connection: BankConnection,
    consent: BankConsent,
) -> None:
    try:
        with build_client(
            provider=connection.bank_provider,
            tenant=connection.tenant,
            correlation_id=consent.correlation_id,
        ) as client:
            status = get_consent_status(client, consent)
            headers = berlin_group_headers(
                connection=connection,
                consent_id=consent.consent_id,
                pre_step_auth=True,
            )
            consent_data_response = client.get(
                f'/v1/consents/{consent.consent_id}',
                headers=headers,
                access_token=consent.access_token,
            )
            access = {}
            if consent_data_response.status_code == 200:
                access = consent_data_response.json().get('access', {})

            has_full_ais = access.get('allPsd2') == 'allAccounts'
            report.add(
                'consent_allpsd2_scope',
                has_full_ais,
                f'access={access}',
            )
            report.add(
                'consent_status_valid',
                status in ('valid', 'authorized'),
                f'consentStatus={status}',
            )

            accounts = list_accounts(client, consent)
            report.add('accounts_fetched', len(accounts) > 0, f'count={len(accounts)}')

            account_id = connection.bank_account.external_account_id
            date_to = timezone.localdate()
            date_from = date_to - timedelta(days=30)
            try:
                fetch_transactions(
                    client,
                    consent,
                    account_id,
                    date_from=date_from,
                    date_to=date_to,
                )
                tx_detail = 'HTTP 200'
                tx_ok = True
            except OtpClientError as exc:
                tx_ok = False
                tx_detail = str(exc)
            report.add('transactions_endpoint', tx_ok, tx_detail)
    except OtpClientError as exc:
        report.add('live_ais_api', False, str(exc))


def _check_sync_state_without_run(report: AisE2eReport, connection: BankConnection) -> None:
    ba = connection.bank_account
    statements = BankStatement.all_objects.filter(
        tenant=connection.tenant,
        bank_account=ba,
    ).count()
    report.add('bank_statement_exists', statements > 0, f'count={statements}')
    report.add(
        'sync_success_streak',
        connection.sync_success_streak >= 2,
        f'streak={connection.sync_success_streak}',
    )
    report.add(
        'last_sync_at',
        connection.last_sync_at is not None,
        str(connection.last_sync_at or 'n/a'),
    )


def _check_sync_pipeline(report: AisE2eReport, connection: BankConnection) -> None:
    ba = connection.bank_account
    before = BankTransaction.all_objects.filter(
        tenant=connection.tenant,
        bank_statement__bank_account=ba,
    ).count()

    try:
        created_first = sync_connection_transactions(connection)
        connection.refresh_from_db()
        created_second = sync_connection_transactions(connection)
        connection.refresh_from_db()
    except Exception as exc:
        report.add('sync_pipeline', False, str(exc))
        return

    after = BankTransaction.all_objects.filter(
        tenant=connection.tenant,
        bank_statement__bank_account=ba,
    ).count()
    statements = BankStatement.all_objects.filter(
        tenant=connection.tenant,
        bank_account=ba,
    ).count()

    report.add('bank_statement_exists', statements > 0, f'count={statements}')
    report.add('sync_first_run', True, f'created={created_first}')
    report.add(
        'sync_idempotent',
        created_second == 0 and after == before + created_first,
        f'created_second={created_second}, total={after}',
    )
    report.add(
        'sync_success_streak',
        connection.sync_success_streak >= 2,
        f'streak={connection.sync_success_streak}',
    )


def resolve_e2e_connection(
    *,
    connection_id: int | None = None,
    tenant_slug: str | None = None,
) -> BankConnection:
    qs = BankConnection.all_objects.select_related(
        'bank_provider',
        'bank_account',
        'tenant',
    )
    if connection_id is not None:
        return qs.get(pk=connection_id)
    if tenant_slug:
        connection = (
            qs.filter(tenant__slug=tenant_slug, status='connected')
            .order_by('-updated_at')
            .first()
        )
        if connection is None:
            raise BankConnection.DoesNotExist(
                f'Nema connected veze za tenant {tenant_slug}.',
            )
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
