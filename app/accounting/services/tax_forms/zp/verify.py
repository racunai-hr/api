"""Verification — ZP cross-form checks and payload/XML diff."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from accounting.services.tax_forms.zp.payload import ZpPayload


@dataclass(frozen=True)
class ZpPdvCrossCheckResult:
    pdv_box_101: Decimal
    pdv_box_103: Decimal
    zp_total_goods: Decimal
    zp_total_services: Decimal
    is_aligned: bool


def verify_zp_against_pdv_boxes(
    payload: ZpPayload,
    *,
    pdv_box_101: Decimal,
    pdv_box_103: Decimal,
) -> ZpPdvCrossCheckResult:
    """Compare ZP totals with PDV Podatak101 / Podatak103."""
    goods = payload.total_goods
    services = payload.total_services
    return ZpPdvCrossCheckResult(
        pdv_box_101=pdv_box_101,
        pdv_box_103=pdv_box_103,
        zp_total_goods=goods,
        zp_total_services=services,
        is_aligned=(goods == pdv_box_101 and services == pdv_box_103),
    )


def compare_zp_payload_fields(left: ZpPayload, right: ZpPayload) -> list[str]:
    """Field-level diff for round-trip and integrity checks."""
    from accounting.services.tax_forms.zp.canonical import payload_to_dict

    left_dict = payload_to_dict(left)
    right_dict = payload_to_dict(right)
    diffs: list[str] = []
    for key in sorted(set(left_dict) | set(right_dict)):
        if left_dict.get(key) != right_dict.get(key):
            diffs.append(f'{key}: {left_dict.get(key)!r} != {right_dict.get(key)!r}')
    return diffs
