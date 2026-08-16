"""VAT supply procedure — OSS/IOSS e-commerce routing to PDV boxes."""

from __future__ import annotations

from django.db import models

# Reported on PDV but excluded from domestic box 200/300 totals and box 400.
OSS_ECOMMERCE_OUTPUT_BOXES = frozenset({'204', '214', '215'})
IOSS_INPUT_BOXES = frozenset({'308'})
ECOMMERCE_EXCLUDED_FROM_VAT_DUE = OSS_ECOMMERCE_OUTPUT_BOXES | IOSS_INPUT_BOXES

_INVOICE_PROCEDURE_TO_BOX = {
    'oss': '215',
    'eu_distance': '204',
    'eu_electronic': '214',
}


class VatSupplyProcedure(models.TextChoices):
    STANDARD = 'standard', 'Standardno'
    OSS = 'oss', 'OSS (e-trgovina EU)'
    EU_DISTANCE = 'eu_distance', 'Prodaja na daljinu EU'
    EU_ELECTRONIC = 'eu_electronic', 'Elektroničko sučelje'
    IOSS = 'ioss', 'IOSS (uvoz)'


def invoice_procedure_to_box(procedure: str) -> str | None:
    """Map invoice line procedure to PDV output box (204/214/215)."""
    if not procedure or procedure == VatSupplyProcedure.STANDARD:
        return None
    return _INVOICE_PROCEDURE_TO_BOX.get(procedure)


def is_ecommerce_output_box(box_code: str) -> bool:
    return box_code in OSS_ECOMMERCE_OUTPUT_BOXES
