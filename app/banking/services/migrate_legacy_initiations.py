"""One-off migracija BankPaymentInitiation → PaymentOrder + PaymentExecution."""

from __future__ import annotations

from typing import TYPE_CHECKING

DEFAULT_PAYMENT_PRODUCT = 'sepa-credit-transfers'

if TYPE_CHECKING:
    from banking.provider_models import BankPaymentInitiation, PaymentOrder

LEGACY_STATUS_MAP = {
    'received': 'submitted',
    'pending': 'sca_required',
    'accepted': 'accepted',
    'rejected': 'rejected',
    'cancelled': 'failed',
}


def migrate_bank_payment_initiation(initiation: BankPaymentInitiation) -> PaymentOrder | None:
    """Kopira legacy inicijaciju u PaymentOrder + PaymentExecution (attempt 1)."""
    from banking.provider_models import PaymentExecution, PaymentOrder

    payment = initiation.payment
    connection = initiation.connection
    debtor_iban = connection.bank_account.iban or ''
    creditor_iban = payment.counterparty_account or ''
    if not debtor_iban or not creditor_iban:
        return None

    order_status = LEGACY_STATUS_MAP.get(initiation.status, 'submitted')

    order = PaymentOrder.all_objects.create(
        tenant=initiation.tenant,
        connection=connection,
        payment=payment,
        status=order_status,
        otp_payment_id=initiation.initiation_id,
        authorization_id=initiation.authorization_id,
        sca_redirect_url=initiation.sca_redirect_url,
        debtor_iban=debtor_iban,
        creditor_iban=creditor_iban,
        creditor_name=payment.counterparty_name or 'Primatelj',
        amount=payment.amount,
        currency=payment.currency,
        reference=payment.reference_number or payment.description or '',
        correlation_id=initiation.correlation_id,
        last_error=initiation.last_error,
        payment_product=DEFAULT_PAYMENT_PRODUCT,
    )

    PaymentExecution.all_objects.create(
        tenant=initiation.tenant,
        order=order,
        attempt=1,
        status=order_status,
        provider_payment_id=initiation.initiation_id,
        authorization_id=initiation.authorization_id,
        sca_redirect_url=initiation.sca_redirect_url,
        payment_product=DEFAULT_PAYMENT_PRODUCT,
        last_error=initiation.last_error,
        correlation_id=initiation.correlation_id,
        parent_order_correlation_id=order.correlation_id,
    )
    return order
