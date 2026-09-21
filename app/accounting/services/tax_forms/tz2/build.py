"""Build Tz2Payload — architectural gate (single aggregation call)."""

from __future__ import annotations

from accounting.services.tax_forms.tz2.aggregate import Tz2BuildInput, aggregate_tz2
from accounting.services.tax_forms.tz2.payload import Tz2Payload


def build_tz2_payload(tax_year: int, build_input: Tz2BuildInput) -> Tz2Payload:
    """Return immutable Tz2Payload for the given calendar year."""
    return aggregate_tz2(tax_year, build_input)
