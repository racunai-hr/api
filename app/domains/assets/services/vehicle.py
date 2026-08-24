"""Vehicle write-path guards. Clean() is admin UX only — this is the invariant."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import FixedAsset, Vehicle


def link_vehicle_to_fixed_asset(vehicle: Vehicle, fixed_asset: FixedAsset) -> Vehicle:
    """Jedini sankcionirani način povezivanja Vehicle ↔ FixedAsset.

    Tvrdo pada na prekršenoj invarijanti. Idempotentno za isti par.
    """
    if vehicle.tenant_id != fixed_asset.tenant_id:
        raise ValidationError({
            'fixed_asset': 'Osnovno sredstvo mora pripadati istom tenantu kao vozilo.',
        })

    vehicle_vin = (vehicle.vin or '').strip().upper()
    asset_vin = (fixed_asset.vin or '').strip().upper()
    if vehicle_vin and asset_vin and vehicle_vin != asset_vin:
        raise ValidationError({
            'fixed_asset': 'VIN vozila mora biti isti kao VIN osnovnog sredstva.',
        })

    try:
        existing = Vehicle.all_objects.get(fixed_asset=fixed_asset)
    except Vehicle.DoesNotExist:
        existing = None
    if existing is not None and existing.pk != vehicle.pk:
        raise ValidationError({
            'fixed_asset': 'Osnovno sredstvo je već vezano na drugo vozilo.',
        })

    if vehicle.fixed_asset_id and vehicle.fixed_asset_id != fixed_asset.pk:
        raise ValidationError({
            'fixed_asset': 'Vozilo je već vezano na drugo osnovno sredstvo.',
        })

    if vehicle.fixed_asset_id == fixed_asset.pk:
        return vehicle

    with transaction.atomic():
        vehicle.fixed_asset = fixed_asset
        vehicle.save(update_fields=['fixed_asset', 'updated_at'])
    return vehicle
