"""Period and tenant locks for VAT projection (Gate D). Lock period before sources."""

from __future__ import annotations

from datetime import date

from django.db import connection, transaction

from accounting.models import VATPeriod
from accounting.services.tax_projection.switch import SwitchState, read_projection_write_switch


def lock_tenant_for_projection(tenant):
    """Serialize rebuild and switch changes for one tenant."""
    from tenants.models import Tenant

    return Tenant.objects.select_for_update().get(pk=tenant.pk)


def lock_open_vat_period_for_source_mutation(tenant, on_date: date) -> VATPeriod | None:
    """Lock open VAT period at the start of a mutation transaction.

    No-op when switch is off/invalid-treated-as-skip, or no open period exists.
    Does not create periods. Call before locking or saving source rows.
    """
    state = read_projection_write_switch(tenant)
    if state != SwitchState.ON:
        return None
    return (
        VATPeriod.all_objects.select_for_update()
        .filter(
            tenant_id=tenant.pk,
            year=on_date.year,
            month=on_date.month,
            status='open',
        )
        .first()
    )


def advisory_lock_tenant(tenant_id: int) -> None:
    """PostgreSQL session advisory lock keyed by tenant id (fallback serialization)."""
    if connection.vendor != 'postgresql':
        return
    # Namespace: VAT projection rebuild (arbitrary stable int).
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', [0x56415400, int(tenant_id)])
