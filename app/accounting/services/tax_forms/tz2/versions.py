"""TZ2Return version allocation with transactional locking."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from accounting.models import TZ2Return
from tenants.models import Tenant


@transaction.atomic
def next_tz2_return_version(tenant: Tenant, tax_year: int) -> int:
    """Lock existing TZ2 rows for the year and return the next version number."""
    list(TZ2Return.all_objects.select_for_update().filter(tenant=tenant, tax_year=tax_year))
    current_max = (
        TZ2Return.all_objects.filter(tenant=tenant, tax_year=tax_year).aggregate(
            max_version=Max('version'),
        )['max_version']
    )
    return (current_max or 0) + 1
