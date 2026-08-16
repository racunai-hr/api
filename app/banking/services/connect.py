from __future__ import annotations

import time
import uuid

from django.db import transaction

from banking.config import resolve_otp_credentials
from banking.otp import ais
from banking.otp.client import build_client
from banking.otp.oauth import build_authorize_url, sign_oauth_state
from banking.provider_models import BankConnection, BankConsent
from banking.services.discovery import discover_accounts_from_api
from payments.models import BankAccount
from tenants.models import Tenant


def start_connect_flow(
    *,
    tenant: Tenant,
    bank_account: BankAccount,
    user_ip: str = '127.0.0.1',
) -> tuple[BankConnection, str]:
    from banking.config import get_active_otp_provider

    provider = get_active_otp_provider()
    if provider is None:
        raise ValueError('OTP provider nije konfiguriran.')

    connection, _ = BankConnection.all_objects.get_or_create(
        tenant=tenant,
        bank_provider=provider,
        bank_account=bank_account,
        defaults={'status': 'pending'},
    )

    credentials = resolve_otp_credentials()
    correlation_id = uuid.uuid4()
    connection.status = 'authorizing'
    connection.last_error = ''
    connection.save(update_fields=['status', 'last_error', 'updated_at'])

    consent = BankConsent.objects.create(
        connection=connection,
        status='received',
        correlation_id=correlation_id,
    )
    state = sign_oauth_state({
        'tenant_slug': tenant.slug,
        'connection_id': connection.pk,
        'consent_id': consent.pk,
        'nonce': str(uuid.uuid4()),
        'exp': time.time() + 3600,
    })
    redirect_url = build_authorize_url(
        iam_base=provider.iam_base,
        client_id=credentials.client_id,
        redirect_uri=credentials.redirect_uri,
        state=state,
    )
    return connection, redirect_url


@transaction.atomic
def handle_oauth_callback(*, code: str, state: str, scope: str = 'OTP.PSD2') -> tuple[BankConnection, str | None]:
    from banking.config import get_active_otp_provider
    from banking.otp.oauth import exchange_code_for_token, verify_oauth_state

    payload = verify_oauth_state(state)
    tenant = Tenant.objects.get(slug=payload['tenant_slug'], is_active=True)
    connection = BankConnection.all_objects.select_related('bank_provider').get(
        pk=payload['connection_id'],
        tenant=tenant,
    )
    consent = BankConsent.objects.get(pk=payload['consent_id'], connection=connection)

    provider = get_active_otp_provider() or connection.bank_provider
    credentials = resolve_otp_credentials()
    sca_url = None
    with build_client(
        provider=provider,
        tenant=tenant,
        correlation_id=consent.correlation_id,
    ) as client:
        token_data = exchange_code_for_token(
            client,
            code,
            credentials.redirect_uri,
            scope=scope,
        )
        consent.access_token = token_data.get('access_token', '')
        consent.refresh_token = token_data.get('refresh_token', '')
        consent.save(update_fields=['access_token', 'refresh_token', 'updated_at'])

        consent, sca_url = ais.create_consent(
            client,
            connection,
            consent=consent,
            access_token=consent.access_token,
        )
        if not sca_url:
            sca_url = ais.start_authorization(
                client,
                consent,
                access_token=consent.access_token,
            )

    if sca_url:
        return connection, sca_url

    ais.finalize_consent_from_token(consent, {
        'access_token': consent.access_token,
        'refresh_token': consent.refresh_token,
    })
    with build_client(
        provider=provider,
        tenant=tenant,
        correlation_id=consent.correlation_id,
    ) as client:
        accounts = ais.list_accounts(client, consent)

    if accounts:
        discover_accounts_from_api(connection, accounts, primary_account=connection.bank_account)

    return connection, None


def _resolve_pending_consent_for_sca() -> BankConsent:
    consent = (
        BankConsent.objects
        .filter(
            connection__status='authorizing',
            consent_id__gt='',
            authorization_id__gt='',
            access_token__gt='',
        )
        .select_related('connection', 'connection__bank_provider', 'connection__tenant')
        .order_by('-updated_at')
        .first()
    )
    if consent is None:
        raise ValueError('Nema consenta u SCA autorizaciji.')
    return consent


@transaction.atomic
def handle_sca_callback(*, confirmation_code: str) -> BankConnection:
    from banking.config import get_active_otp_provider

    consent = _resolve_pending_consent_for_sca()
    connection = consent.connection
    tenant = connection.tenant
    provider = get_active_otp_provider() or connection.bank_provider

    with build_client(
        provider=provider,
        tenant=tenant,
        correlation_id=consent.correlation_id,
    ) as client:
        sca_status = ais.confirm_authorisation(
            client,
            consent,
            confirmation_code,
            access_token=consent.access_token,
        )
        if sca_status not in ('finalised', 'finalized'):
            raise ValueError(f'SCA nije finaliziran (status: {sca_status or "nepoznat"}).')

        consent_status = ais.get_consent_status(
            client,
            consent,
            access_token=consent.access_token,
        )
        if consent_status not in ('valid', 'authorized'):
            raise ValueError(f'Consent nije valjan (status: {consent_status or "nepoznat"}).')

        ais.finalize_authorized_consent(consent)
        accounts = ais.list_accounts(client, consent)

    if accounts:
        discover_accounts_from_api(connection, accounts, primary_account=connection.bank_account)

    return connection


def list_linked_accounts(connection: BankConnection) -> list[dict]:
    consent = connection.get_active_consent()
    if consent is None:
        return []

    with build_client(
        provider=connection.bank_provider,
        tenant=connection.tenant,
        correlation_id=consent.correlation_id,
    ) as client:
        return ais.list_accounts(client, consent)
