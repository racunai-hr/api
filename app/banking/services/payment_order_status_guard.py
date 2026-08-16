"""Runtime guard: PaymentOrder.status smije se mijenjati samo unutar lifecycle konteksta."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

_guard = contextvars.ContextVar('payment_order_lifecycle_active', default=False)


class PaymentOrderStatusGuardError(RuntimeError):
    """Direktna promjena PaymentOrder.status izvan PaymentOrderLifecycle."""


def is_lifecycle_transition_active() -> bool:
    return _guard.get()


@contextmanager
def lifecycle_status_change():
    token = _guard.set(True)
    try:
        yield
    finally:
        _guard.reset(token)
