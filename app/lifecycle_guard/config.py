from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class LifecycleProtectedModel:
    model: str
    allowed_modules: frozenset[Path]
    field: str = 'status'
    scan_packages: frozenset[str] = frozenset({'banking'})


LIFECYCLE_PROTECTED_MODELS: tuple[LifecycleProtectedModel, ...] = (
    LifecycleProtectedModel(
        model='PaymentOrder',
        field='status',
        allowed_modules=frozenset({
            Path('banking/services/payment_order_lifecycle.py'),
        }),
        scan_packages=frozenset({'banking'}),
    ),
    LifecycleProtectedModel(
        model='PaymentExecution',
        field='status',
        allowed_modules=frozenset({
            Path('banking/services/payment_execution_lifecycle.py'),
        }),
        scan_packages=frozenset({'banking'}),
    ),
)
