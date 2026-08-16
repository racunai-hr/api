from __future__ import annotations

from banking.provider_models import BankConnection, PaymentOrder
from banking.services.pis_orders import (
    create_draft_payment_order,
    refresh_payment_order_status,
    submit_domestic_payment_order,
)
from payments.models import Payment


def start_payment_initiation(
    connection: BankConnection,
    payment: Payment,
    *,
    psu_ip: str = '127.0.0.1',
    min_sync_streak: int = 3,
) -> PaymentOrder:
    """Kreira PIS nalog iz Payment zapisa i šalje ga u OTP."""
    if not payment.counterparty_account:
        raise ValueError('Plaćanje nema IBAN primatelja (counterparty_account).')

    order = create_draft_payment_order(
        connection,
        creditor_iban=payment.counterparty_account,
        creditor_name=payment.counterparty_name or 'Primatelj',
        amount=payment.amount,
        currency=payment.currency,
        reference=payment.reference_number or payment.description or '',
        payment=payment,
    )
    submit_domestic_payment_order(
        order,
        psu_ip=psu_ip,
        min_sync_streak=min_sync_streak,
    )
    order.refresh_from_db()
    return order


def refresh_payment_status(order: PaymentOrder) -> PaymentOrder:
    """Osvježava status PIS naloga iz OTP-a."""
    return refresh_payment_order_status(order)
