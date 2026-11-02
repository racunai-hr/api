"""Resolve source-document cost center into a bookable MT for posting."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from accounting.models import CostCenter
from accounting.services.journal_lines import validate_cost_center_for_account


def load_bookable_cost_center(tenant, cost_center_id: int, *, field: str = 'cost_center_id') -> CostCenter:
    cost_center = CostCenter.all_objects.filter(tenant=tenant, pk=cost_center_id).first()
    if cost_center is None:
        raise ValidationError({field: 'Mjesto troška nije pronađeno.'})
    if not cost_center.is_active:
        raise ValidationError({field: 'Mjesto troška nije aktivno.'})
    if not cost_center.is_bookable:
        raise ValidationError({field: 'Grupa mjesta troška nije knjiživa.'})
    return cost_center


def resolve_cost_center(source) -> CostCenter | None:
    """Document MT is resolver input only — never rewrite posted JournalEntryLine rows.

    Priority: source.cost_center → source.vehicle.cost_center →
    source.category.default_cost_center → None.
    """
    explicit = getattr(source, 'cost_center', None)
    if explicit is not None and explicit.is_bookable:
        return explicit

    vehicle = getattr(source, 'vehicle', None)
    vehicle_cc = getattr(vehicle, 'cost_center', None) if vehicle is not None else None
    if vehicle_cc is not None and vehicle_cc.is_bookable:
        return vehicle_cc

    category = getattr(source, 'category', None)
    default_cc = getattr(category, 'default_cost_center', None) if category is not None else None
    if default_cc is not None and default_cc.is_bookable:
        return default_cc

    return None


def apply_cost_center(account, cost_center: CostCenter | None) -> CostCenter | None:
    """Attach MT only when the account is an RDG class; otherwise None."""
    if cost_center is None:
        return None
    try:
        validate_cost_center_for_account(account, cost_center)
    except ValidationError:
        return None
    return cost_center
