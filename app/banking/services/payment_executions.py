"""PaymentExecution helpers — izvor istine je Execution, Order je agregat.

Sinkronizacija je strogo jednosmjerna::

    Execution  ──sync_order_from_execution()──►  Order

nikad obrnuto. Status Order-a ide preko PaymentOrderLifecycle (poziv iz ExecutionLifecycle).

``parent_order_correlation_id`` je immutable snapshot ``order.correlation_id`` pri stvaranju
executiona. Namjerno se ne održava sinkroniziranim — za SQL/log/audit export bez JOIN-a.
"""

from __future__ import annotations

import uuid

from django.db.models import Max

from banking.otp.pis import OTP_DEFAULT_PAYMENT_PRODUCT
from banking.provider_models import PaymentExecution, PaymentOrder

# Polja koja se denormaliziraju s Execution na Order (agregat zadnjeg pokušaja).
_ORDER_SYNC_FIELDS = (
    'otp_payment_id',
    'authorization_id',
    'sca_redirect_url',
    'payment_product',
    'last_error',
)


def get_latest_execution(order: PaymentOrder) -> PaymentExecution | None:
    return order.executions.order_by('-attempt').first()


def next_execution_attempt(order: PaymentOrder) -> int:
    current = order.executions.aggregate(max_attempt=Max('attempt')).get('max_attempt') or 0
    return current + 1


def resolve_execution_correlation_id(order: PaymentOrder, *, attempt: int) -> uuid.UUID:
    """Pravilo za correlation_id po pokušaju.

    - attempt 1: nasljeđuje Order.correlation_id (isti trag od draft naloga)
    - attempt ≥ 2: novi UUID (novi bankovni pokušaj, Order PK ostaje isti)
    """
    if attempt == 1:
        return order.correlation_id
    return uuid.uuid4()


def create_payment_execution(order: PaymentOrder) -> PaymentExecution:
    attempt = next_execution_attempt(order)
    return PaymentExecution.all_objects.create(
        tenant=order.tenant,
        order=order,
        attempt=attempt,
        status='draft',
        payment_product=order.payment_product or OTP_DEFAULT_PAYMENT_PRODUCT,
        correlation_id=resolve_execution_correlation_id(order, attempt=attempt),
        parent_order_correlation_id=order.correlation_id,
    )


def sync_order_from_execution(order: PaymentOrder, execution: PaymentExecution) -> None:
    """Denormalizira API polja s Execution na Order (agregat).

    Jednosmjerno: Execution → Order. Order.correlation_id se ne prepisuje ovdje —
    agregatni ID naloga ostaje stabilan; po pokušaju koristi se execution.correlation_id
    u API pozivima i execution audit tragu.
    """
    order.otp_payment_id = execution.provider_payment_id
    order.authorization_id = execution.authorization_id
    order.sca_redirect_url = execution.sca_redirect_url
    order.payment_product = execution.payment_product
    order.last_error = execution.last_error
    order.save(
        update_fields=[*_ORDER_SYNC_FIELDS, 'updated_at'],
    )
