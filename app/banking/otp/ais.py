from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

from banking.otp.client import OtpClientError, OtpHttpClient
from banking.otp.headers import berlin_group_headers
from banking.provider_models import BankConnection, BankConsent


def create_consent(
    client: OtpHttpClient,
    connection: BankConnection,
    *,
    valid_until: date | None = None,
    access_token: str | None = None,
    consent: BankConsent | None = None,
) -> tuple[BankConsent, str]:
    if valid_until is None:
        valid_until = timezone.localdate() + timedelta(days=90)

    if consent is None:
        consent = BankConsent.objects.create(
            connection=connection,
            status='received',
            valid_until=valid_until,
            correlation_id=client.correlation_id,
        )
    elif consent.valid_until is None:
        consent.valid_until = valid_until
        consent.save(update_fields=['valid_until', 'updated_at'])

    payload = {
        'access': {'allPsd2': 'allAccounts'},
        'recurringIndicator': True,
        'validUntil': valid_until.isoformat(),
        'frequencyPerDay': 4,
        'combinedServiceIndicator': False,
    }
    headers = berlin_group_headers(
        connection=connection,
        redirect_uri=client.credentials.redirect_uri,
        pre_step_auth=bool(access_token),
    )
    response = client.post(
        '/v1/consents',
        headers=headers,
        json=payload,
        access_token=access_token,
    )
    if response.status_code >= 400:
        consent.status = 'rejected'
        consent.save(update_fields=['status', 'updated_at'])
        raise OtpClientError(
            f'Consent creation failed: {response.status_code} {(response.text or "")[:200]}',
        )

    data = response.json()
    consent.consent_id = data.get('consentId', '')
    consent.status = data.get('consentStatus', 'received').lower()
    consent.save(update_fields=['consent_id', 'status', 'updated_at'])
    sca_redirect = _extract_sca_redirect(data)
    return consent, sca_redirect


def _extract_sca_redirect(data: dict) -> str:
    links = data.get('_links') or {}
    sca = links.get('scaRedirect') or {}
    href = sca.get('href') if isinstance(sca, dict) else ''
    return href or ''


def start_authorization(
    client: OtpHttpClient,
    consent: BankConsent,
    *,
    psu_ip: str = '127.0.0.1',
    access_token: str | None = None,
) -> str:
    headers = berlin_group_headers(
        connection=consent.connection,
        consent_id=consent.consent_id,
        redirect_uri=client.credentials.redirect_uri,
        psu_ip=psu_ip,
        pre_step_auth=True,
    )
    response = client.post(
        f'/v1/consents/{consent.consent_id}/authorisations',
        headers=headers,
        json={},
        access_token=access_token or consent.access_token or None,
    )
    if response.status_code >= 400:
        raise OtpClientError(
            f'Authorization start failed: {response.status_code} {(response.text or "")[:200]}',
        )

    data = response.json()
    consent.authorization_id = data.get('authorisationId', '')
    consent.save(update_fields=['authorization_id', 'updated_at'])
    sca_redirect = _extract_sca_redirect(data)
    if not sca_redirect:
        raise OtpClientError('SCA redirect URL nije vraćen.')
    return sca_redirect


def confirm_authorisation(
    client: OtpHttpClient,
    consent: BankConsent,
    confirmation_code: str,
    *,
    access_token: str | None = None,
) -> str:
    if not consent.authorization_id:
        raise OtpClientError('Nedostaje authorization_id za SCA potvrdu.')

    headers = berlin_group_headers(
        connection=consent.connection,
        consent_id=consent.consent_id,
        pre_step_auth=True,
    )
    response = client.put(
        f'/v1/consents/{consent.consent_id}/authorisations/{consent.authorization_id}',
        headers=headers,
        json={'confirmationCode': confirmation_code},
        access_token=access_token or consent.access_token or None,
    )
    if response.status_code >= 400:
        raise OtpClientError(
            f'SCA confirmation failed: {response.status_code} {(response.text or "")[:200]}',
        )

    data = response.json()
    return data.get('scaStatus', '').lower()


def get_consent_status(
    client: OtpHttpClient,
    consent: BankConsent,
    *,
    access_token: str | None = None,
) -> str:
    headers = berlin_group_headers(
        connection=consent.connection,
        consent_id=consent.consent_id,
        pre_step_auth=True,
    )
    response = client.get(
        f'/v1/consents/{consent.consent_id}/status',
        headers=headers,
        access_token=access_token or consent.access_token or None,
    )
    if response.status_code >= 400:
        raise OtpClientError(
            f'Consent status failed: {response.status_code} {(response.text or "")[:200]}',
        )

    data = response.json()
    return data.get('consentStatus', '').lower()


def finalize_authorized_consent(consent: BankConsent) -> BankConsent:
    consent.status = 'authorized'
    consent.save(update_fields=['status', 'updated_at'])
    connection = consent.connection
    connection.status = 'connected'
    connection.last_error = ''
    connection.save(update_fields=['status', 'last_error', 'updated_at'])
    return consent


def finalize_consent_from_token(consent: BankConsent, token_data: dict) -> BankConsent:
    consent.access_token = token_data.get('access_token', '')
    consent.refresh_token = token_data.get('refresh_token', '')
    consent.save(update_fields=['access_token', 'refresh_token', 'updated_at'])
    return finalize_authorized_consent(consent)


def list_accounts(client: OtpHttpClient, consent: BankConsent) -> list[dict]:
    headers = berlin_group_headers(
        connection=consent.connection,
        consent_id=consent.consent_id,
        pre_step_auth=True,
    )
    response = client.get(
        '/v1/accounts',
        headers=headers,
        access_token=consent.access_token,
    )
    if response.status_code >= 400:
        raise OtpClientError(f'Accounts fetch failed: {response.status_code}')
    data = response.json()
    return data.get('accounts', [])


def fetch_transactions(
    client: OtpHttpClient,
    consent: BankConsent,
    account_id: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    booking_status: str = 'booked',
) -> list[dict]:
    headers = berlin_group_headers(
        connection=consent.connection,
        consent_id=consent.consent_id,
        pre_step_auth=True,
    )
    params: dict[str, str] = {'bookingStatus': booking_status}
    if date_from:
        params['dateFrom'] = date_from.isoformat()
    if date_to:
        params['dateTo'] = date_to.isoformat()

    booked: list[dict] = []
    next_path = f'/v1/accounts/{account_id}/transactions'
    while next_path:
        response = client.get(
            next_path,
            headers=headers,
            params=params if next_path.endswith('/transactions') else None,
            access_token=consent.access_token,
        )
        if response.status_code >= 400:
            raise OtpClientError(
                f'Transactions fetch failed: {response.status_code} {(response.text or "")[:200]}',
            )

        data = response.json()
        transactions = data.get('transactions', {})
        if isinstance(transactions, dict):
            booked.extend(transactions.get('booked', []))
        else:
            booked.extend(data.get('booked', []))

        links = data.get('transactions', {}).get('_links', {}) if isinstance(data.get('transactions'), dict) else data.get('_links', {})
        next_link = links.get('next', {}) if isinstance(links, dict) else {}
        next_href = next_link.get('href', '') if isinstance(next_link, dict) else ''
        if next_href:
            if next_href.startswith('http'):
                next_path = next_href
            else:
                next_path = next_href
            params = {}
        else:
            next_path = ''

    return booked
