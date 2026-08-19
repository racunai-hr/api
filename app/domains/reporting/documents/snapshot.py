"""Consistent database snapshot for one request (ADR-0020 §5)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from django.db import connection, transaction
from django.utils import timezone


@contextmanager
def read_snapshot():
    """Yield UTC `as_of` inside one atomic read.

    PostgreSQL `REPEATABLE READ` is set only when this call can start a fresh
    transaction. Nested atomic blocks (Django `TestCase`, `ATOMIC_REQUESTS`)
    cannot change isolation after queries have already run; the outer
    transaction still gives a consistent snapshot for that request.
    """
    use_repeatable_read = (
        connection.vendor == 'postgresql' and not connection.in_atomic_block
    )
    with transaction.atomic():
        if use_repeatable_read:
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        yield timezone.now()


def isoformat(as_of: datetime) -> str:
    return as_of.isoformat()
