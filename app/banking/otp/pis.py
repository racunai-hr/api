from __future__ import annotations

from banking.otp.ais import _extract_sca_redirect
from banking.otp.client import OtpClientError, OtpHttpClient
from banking.otp.headers import berlin_group_headers
from banking.provider_models import BankConsent, PaymentExecution, PaymentOrder
from banking.services.payment_execution_lifecycle import (
    ACTOR_OTP_CALLBACK,
    ACTOR_OTP_POLLING,
    ACTOR_SUBMIT,
    ExecutionLifecycle,
)

DOMESTIC_PAYMENT = 'domestic-payment'
# OTP sandbox: EUR domaća plaćanja idu preko SEPA (domestic-payment → PRODUCT_UNKNOWN).
OTP_DEFAULT_PAYMENT_PRODUCT = 'sepa-credit-transfers'

from banking.services.payment_executions import sync_order_from_execution

# Viši rank = napredniji status; terminalni statusi se ne prepisuju nižim.
PAYMENT_STATUS_RANK = {
    'draft': 0,
    'submitted': 10,
    'sca_required': 20,
    'authorised': 30,
    'accepted': 40,
    'executed': 50,
    'rejected': 60,
    'failed': 60,
}
TERMINAL_PAYMENT_STATUSES = frozenset({'executed', 'rejected', 'failed'})


def map_otp_payment_status(api_status: str) -> str:
    mapping = {
        'rcvd': 'submitted',
        'pdng': 'sca_required',
        'actc': 'sca_required',
        'accp': 'accepted',
        'acsp': 'accepted',
        'acsc': 'executed',
        'rjct': 'rejected',
        'canc': 'failed',
    }
    return mapping.get(api_status.lower(), 'submitted')


def resolve_payment_order_status(
    current: str,
    *,
    transaction_status: str,
    sca_status: str = '',
) -> str:
    """Mapira OTP status bez degradacije (npr. authorised → submitted)."""
    if current in TERMINAL_PAYMENT_STATUSES:
        return current

    mapped = map_otp_payment_status(transaction_status)
    sca = sca_status.lower()
    if sca in ('finalised', 'finalized'):
        if mapped in ('submitted', 'sca_required'):
            mapped = 'authorised'

    current_rank = PAYMENT_STATUS_RANK.get(current, 0)
    mapped_rank = PAYMENT_STATUS_RANK.get(mapped, 0)
    if mapped_rank < current_rank:
        return current
    return mapped


def _payment_path(product: str, payment_id: str, suffix: str = '') -> str:
    base = f'/v1/payments/{product}/{payment_id}'
    return f'{base}/{suffix.lstrip("/")}' if suffix else base


def _build_domestic_payload(order: PaymentOrder) -> dict:
    return {
        'debtorAccount': {
            'iban': order.debtor_iban,
            'currency': order.currency,
        },
        'instructedAmount': {
            'currency': order.currency,
            'amount': str(order.amount),
        },
        'creditorAccount': {
            'iban': order.creditor_iban,
        },
        'creditorName': order.creditor_name,
        'remittanceInformationUnstructured': order.reference or f'PIS-{order.pk}',
    }


def initiate_domestic_payment(
    client: OtpHttpClient,
    order: PaymentOrder,
    execution: PaymentExecution,
    *,
    psu_ip: str = '127.0.0.1',
) -> PaymentExecution:
    consent = _require_consent(order)
    headers = berlin_group_headers(
        connection=order.connection,
        consent_id=consent.consent_id,
        redirect_uri=client.credentials.redirect_uri,
        psu_ip=psu_ip,
        pre_step_auth=True,
    )
    response = client.post(
        f'/v1/payments/{execution.payment_product}',
        headers=headers,
        json=_build_domestic_payload(order),
        access_token=consent.access_token,
    )
    if response.status_code >= 400:
        execution.last_error = (response.text or '')[:500]
        ExecutionLifecycle.transition(
            execution,
            'failed',
            actor=ACTOR_SUBMIT,
            reason=f'HTTP {response.status_code}',
            metadata={'api_status': response.status_code},
        )
        execution.save(update_fields=['last_error', 'updated_at'])
        sync_order_from_execution(order, execution)
        raise OtpClientError(
            f'Payment initiation failed: {response.status_code} {execution.last_error[:200]}',
        )

    data = response.json()
    execution.provider_payment_id = data.get('paymentId', '')
    api_status = data.get('transactionStatus', 'RCVD')
    candidate = resolve_payment_order_status(
        execution.status,
        transaction_status=api_status,
    )
    ExecutionLifecycle.transition(
        execution,
        candidate,
        actor=ACTOR_SUBMIT,
        metadata={'api_status': api_status},
    )
    sca_redirect = _extract_sca_redirect(data)
    if sca_redirect:
        ExecutionLifecycle.transition(
            execution,
            'sca_required',
            actor=ACTOR_SUBMIT,
            metadata={'sca_redirect': True},
        )
        execution.sca_redirect_url = sca_redirect

    auth_id = data.get('authorisationId', '')
    if not auth_id:
        links = data.get('_links') or {}
        start_auth = links.get('startAuthorisation') or {}
        if isinstance(start_auth, dict) and start_auth.get('href'):
            auth_id = _extract_authorisation_id_from_links(data)
    execution.authorization_id = auth_id or execution.authorization_id
    execution.last_error = ''
    execution.save(
        update_fields=[
            'provider_payment_id',
            'sca_redirect_url',
            'authorization_id',
            'last_error',
            'updated_at',
        ],
    )
    sync_order_from_execution(order, execution)
    return execution


def _extract_authorisation_id_from_links(data: dict) -> str:
    links = data.get('_links') or {}
    for key in ('startAuthorisation', 'authorisations'):
        link = links.get(key) or {}
        href = link.get('href', '') if isinstance(link, dict) else ''
        if '/authorisations/' in href:
            return href.rstrip('/').split('/')[-1]
    return ''


def confirm_payment_authorisation(
    client: OtpHttpClient,
    order: PaymentOrder,
    execution: PaymentExecution,
    confirmation_code: str,
    *,
    access_token: str | None = None,
) -> str:
    consent = _require_consent(order)
    if not execution.provider_payment_id or not execution.authorization_id:
        raise OtpClientError('Nedostaje provider_payment_id ili authorization_id za SCA potvrdu.')

    headers = berlin_group_headers(
        connection=order.connection,
        consent_id=consent.consent_id,
        pre_step_auth=True,
    )
    path = _payment_path(
        execution.payment_product,
        execution.provider_payment_id,
        f'authorisations/{execution.authorization_id}',
    )
    response = client.put(
        path,
        headers=headers,
        json={'confirmationCode': confirmation_code},
        access_token=access_token or consent.access_token or None,
    )
    if response.status_code >= 400:
        raise OtpClientError(
            f'Payment SCA confirmation failed: {response.status_code} {(response.text or "")[:200]}',
        )

    data = response.json()
    sca_status = data.get('scaStatus', '').lower()
    if sca_status in ('finalised', 'finalized'):
        candidate = resolve_payment_order_status(
            execution.status,
            transaction_status='RCVD',
            sca_status=sca_status,
        )
        ExecutionLifecycle.transition(
            execution,
            candidate,
            actor=ACTOR_OTP_CALLBACK,
            metadata={'sca_status': sca_status},
        )
    return sca_status


def poll_payment_order_status(
    client: OtpHttpClient,
    order: PaymentOrder,
    execution: PaymentExecution,
) -> PaymentExecution:
    consent = _require_consent(order)
    if not execution.provider_payment_id:
        raise OtpClientError('Nedostaje provider_payment_id.')

    headers = berlin_group_headers(
        connection=order.connection,
        consent_id=consent.consent_id,
        pre_step_auth=True,
    )
    path = _payment_path(execution.payment_product, execution.provider_payment_id, 'status')
    response = client.get(
        path,
        headers=headers,
        access_token=consent.access_token,
    )
    if response.status_code >= 400:
        execution.last_error = (response.text or '')[:500]
        execution.save(update_fields=['last_error', 'updated_at'])
        sync_order_from_execution(order, execution)
        raise OtpClientError(
            f'Payment status failed: {response.status_code} {execution.last_error[:200]}',
        )

    data = response.json()
    api_status = data.get('transactionStatus', '')
    sca_status = _fetch_payment_sca_status(client, order, execution, consent)
    candidate = resolve_payment_order_status(
        execution.status,
        transaction_status=api_status,
        sca_status=sca_status,
    )
    ExecutionLifecycle.transition(
        execution,
        candidate,
        actor=ACTOR_OTP_POLLING,
        metadata={'api_status': api_status, 'sca_status': sca_status},
    )
    execution.last_error = ''
    execution.save(update_fields=['last_error', 'updated_at'])
    sync_order_from_execution(order, execution)
    return execution


def _require_consent(order: PaymentOrder) -> BankConsent:
    consent = order.connection.get_active_consent()
    if consent is None:
        raise OtpClientError('Nema aktivnog consenta za plaćanje.')
    return consent


def _fetch_payment_sca_status(
    client: OtpHttpClient,
    order: PaymentOrder,
    execution: PaymentExecution,
    consent: BankConsent,
) -> str:
    if not execution.authorization_id or not execution.provider_payment_id:
        return ''
    headers = berlin_group_headers(
        connection=order.connection,
        consent_id=consent.consent_id,
        pre_step_auth=True,
    )
    path = _payment_path(
        execution.payment_product,
        execution.provider_payment_id,
        f'authorisations/{execution.authorization_id}',
    )
    response = client.get(
        path,
        headers=headers,
        access_token=consent.access_token,
    )
    if response.status_code >= 400:
        return ''
    return response.json().get('scaStatus', '')
