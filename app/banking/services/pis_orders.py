from __future__ import annotations

import time
from decimal import Decimal

from django.db import transaction

from banking.otp.client import build_client
from banking.otp.pis import OTP_DEFAULT_PAYMENT_PRODUCT, initiate_domestic_payment, poll_payment_order_status, confirm_payment_authorisation
from banking.provider_models import BankConnection, PaymentExecution, PaymentOrder
from banking.services.payment_execution_lifecycle import ACTOR_SUBMIT, ExecutionLifecycle
from banking.services.payment_executions import (
    create_payment_execution,
    get_latest_execution,
    sync_order_from_execution,
)
from payments.models import Payment

SUBMITTABLE_ORDER_STATUSES = frozenset({'draft', 'rejected', 'failed'})


def create_draft_payment_order(
    connection: BankConnection,
    *,
    creditor_iban: str,
    creditor_name: str,
    amount: Decimal,
    currency: str = 'EUR',
    reference: str = '',
    payment: Payment | None = None,
) -> PaymentOrder:
    debtor_iban = connection.bank_account.iban or ''
    if not debtor_iban:
        raise ValueError('Debtor IBAN nije postavljen na bankovnom računu.')

    return PaymentOrder.all_objects.create(
        tenant=connection.tenant,
        connection=connection,
        payment=payment,
        status='draft',
        debtor_iban=debtor_iban,
        creditor_iban=creditor_iban,
        creditor_name=creditor_name,
        amount=amount,
        currency=currency,
        reference=reference,
        payment_product=OTP_DEFAULT_PAYMENT_PRODUCT,
    )


@transaction.atomic
def submit_domestic_payment_order(
    order: PaymentOrder,
    *,
    psu_ip: str = '127.0.0.1',
    min_sync_streak: int = 3,
) -> PaymentOrder:
    if order.status not in SUBMITTABLE_ORDER_STATUSES:
        raise ValueError(
            f'Nalog nije u statusu za slanje (trenutno: {order.status}). '
            f'Dozvoljeno: {", ".join(sorted(SUBMITTABLE_ORDER_STATUSES))}.',
        )

    connection = order.connection
    if not connection.is_pis_ready(min_streak=min_sync_streak):
        raise ValueError(
            f'PIS nije dopušten: potrebno ≥{min_sync_streak} uzastopna AIS synca '
            f'(trenutni streak: {connection.sync_success_streak}).',
        )

    execution = create_payment_execution(order)

    with build_client(
        provider=connection.bank_provider,
        tenant=connection.tenant,
        correlation_id=execution.correlation_id,
    ) as client:
        initiate_domestic_payment(client, order, execution, psu_ip=psu_ip)
        order.refresh_from_db()
        execution.refresh_from_db()
        if execution.status == 'submitted' and not execution.sca_redirect_url:
            _try_start_payment_authorisation(client, order, execution, psu_ip=psu_ip)

    return order


def _try_start_payment_authorisation(
    client,
    order: PaymentOrder,
    execution: PaymentExecution,
    *,
    psu_ip: str,
) -> None:
    from banking.otp.ais import _extract_sca_redirect
    from banking.otp.headers import berlin_group_headers

    consent = order.connection.get_active_consent()
    if consent is None or not execution.provider_payment_id:
        return

    headers = berlin_group_headers(
        connection=order.connection,
        consent_id=consent.consent_id,
        redirect_uri=client.credentials.redirect_uri,
        psu_ip=psu_ip,
        pre_step_auth=True,
    )
    response = client.post(
        f'/v1/payments/{execution.payment_product}/{execution.provider_payment_id}/authorisations',
        headers=headers,
        json={},
        access_token=consent.access_token,
    )
    if response.status_code >= 400:
        return

    data = response.json()
    execution.authorization_id = data.get('authorisationId', '') or execution.authorization_id
    sca_redirect = _extract_sca_redirect(data)
    if sca_redirect:
        ExecutionLifecycle.transition(
            execution,
            'sca_required',
            actor=ACTOR_SUBMIT,
            metadata={'authorisation_started': True},
        )
        execution.sca_redirect_url = sca_redirect
        execution.save(update_fields=['authorization_id', 'sca_redirect_url', 'updated_at'])
        sync_order_from_execution(order, execution)


def resolve_pending_payment_order() -> PaymentOrder | None:
    execution = (
        PaymentExecution.all_objects
        .filter(status='sca_required', provider_payment_id__gt='')
        .select_related('order', 'order__connection', 'order__connection__bank_provider', 'order__connection__tenant')
        .order_by('-updated_at')
        .first()
    )
    return execution.order if execution else None


@transaction.atomic
def handle_payment_sca_callback(*, confirmation_code: str) -> PaymentOrder:
    order = resolve_pending_payment_order()
    if order is None:
        raise ValueError('Nema PIS naloga u SCA autorizaciji.')

    execution = get_latest_execution(order)
    if execution is None:
        raise ValueError('Nalog nema aktivnog izvršenja.')

    connection = order.connection
    with build_client(
        provider=connection.bank_provider,
        tenant=connection.tenant,
        correlation_id=execution.correlation_id,
    ) as client:
        confirm_payment_authorisation(client, order, execution, confirmation_code)
        poll_payment_order_status(client, order, execution)
        order.refresh_from_db()
        execution.refresh_from_db()
        if execution.status in ('submitted', 'sca_required'):
            time.sleep(2)
            poll_payment_order_status(client, order, execution)

    return order


def refresh_payment_order_status(order: PaymentOrder) -> PaymentOrder:
    execution = get_latest_execution(order)
    if execution is None:
        raise ValueError('Nalog nema izvršenja za osvježavanje statusa.')

    connection = order.connection
    with build_client(
        provider=connection.bank_provider,
        tenant=connection.tenant,
        correlation_id=execution.correlation_id,
    ) as client:
        poll_payment_order_status(client, order, execution)

    return order
