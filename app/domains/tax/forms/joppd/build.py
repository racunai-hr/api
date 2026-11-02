"""Build JoppdPayload — architectural gate (single aggregation call)."""

from __future__ import annotations

from domains.tax.forms.joppd.aggregate import aggregate_joppd_rows
from domains.tax.forms.joppd.payload import JoppdPayload


def build_joppd_payload(tenant, year: int, month: int) -> JoppdPayload:
    """Return immutable JoppdPayload for the given tenant/year/month."""
    return aggregate_joppd_rows(tenant, year, month)
