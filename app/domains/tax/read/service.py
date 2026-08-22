"""PDV Razdoblja collection read-model."""

from __future__ import annotations

from django.db.models import Exists, OuterRef

from accounting.models import VATLedgerEntry, VATPeriod
from domains.tax.read.dto import pdv_period_dto


def list_pdv_periods(tenant) -> dict:
    qs = (
        VATPeriod.all_objects.filter(tenant=tenant)
        .annotate(
            has_ledger=Exists(
                VATLedgerEntry.all_objects.filter(vat_period_id=OuterRef('pk')),
            ),
        )
        .order_by('-year', '-month')
    )
    results = [pdv_period_dto(period, has_ledger=period.has_ledger) for period in qs]
    return {'count': len(results), 'results': results}
