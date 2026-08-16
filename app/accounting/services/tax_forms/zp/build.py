"""Build ZpPayload — architectural gate (single aggregation call)."""

from __future__ import annotations

from accounting.models import VATPeriod
from accounting.services.tax_forms.zp.aggregate import aggregate_zp_rows
from accounting.services.tax_forms.zp.payload import ZpPayload


def build_zp_payload(period: VATPeriod) -> ZpPayload:
    """Return immutable ZpPayload for the given VAT period."""
    return aggregate_zp_rows(period)
