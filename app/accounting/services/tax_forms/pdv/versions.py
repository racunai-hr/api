"""VATReturn version allocation with transactional locking."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from accounting.models import VATPeriod, VATReturn


@transaction.atomic
def next_vat_return_version(period: VATPeriod) -> int:
    """Lock VATPeriod and return the next VATReturn version number."""
    VATPeriod.all_objects.select_for_update().get(pk=period.pk)
    current_max = (
        VATReturn.all_objects.filter(vat_period=period).aggregate(max_version=Max('version'))['max_version']
    )
    return (current_max or 0) + 1
