"""ZPReturn version allocation with transactional locking."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from accounting.models import VATPeriod, ZPReturn


@transaction.atomic
def next_zp_return_version(period: VATPeriod) -> int:
    """Lock VATPeriod and return the next ZPReturn version number."""
    VATPeriod.all_objects.select_for_update().get(pk=period.pk)
    current_max = (
        ZPReturn.all_objects.filter(vat_period=period).aggregate(max_version=Max('version'))['max_version']
    )
    return (current_max or 0) + 1
