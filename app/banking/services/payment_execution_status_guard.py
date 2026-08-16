"""Runtime guard: PaymentExecution.status smije se mijenjati samo unutar lifecycle konteksta."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

_guard = contextvars.ContextVar('payment_execution_lifecycle_active', default=False)


class PaymentExecutionStatusGuardError(RuntimeError):
    """Direktna promjena PaymentExecution.status izvan ExecutionLifecycle."""


def is_lifecycle_transition_active() -> bool:
    return _guard.get()


@contextmanager
def lifecycle_status_change():
    token = _guard.set(True)
    try:
        yield
    finally:
        _guard.reset(token)
