"""JOPPDReturn version allocation with transactional locking."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from accounting.models import JOPPDReturn


@transaction.atomic
def next_joppd_return_version(tenant, year: int, month: int) -> int:
    """Lock existing JOPPD rows for the period and return the next version number."""
    list(
        JOPPDReturn.all_objects.select_for_update().filter(
            tenant=tenant, period_year=year, period_month=month,
        )
    )
    current_max = (
        JOPPDReturn.all_objects.filter(
            tenant=tenant, period_year=year, period_month=month,
        ).aggregate(max_version=Max('version'))['max_version']
    )
    return (current_max or 0) + 1
