"""Tenant cost-center codebook (mjesta troška)."""

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.http import Http404

from accounting.models import CostCenter, CostCenterKind, Vehicle

HOSPITALITY_PRESET = (
    {'code': '1', 'name': 'Ugostiteljstvo', 'kind': CostCenterKind.GROUP, 'parent_code': None},
    {'code': '100', 'name': 'Restoran', 'kind': CostCenterKind.LOCATION, 'parent_code': '1'},
    {'code': '110', 'name': 'Kuhinja', 'kind': CostCenterKind.LOCATION, 'parent_code': '1'},
    {'code': '120', 'name': 'Bar / točionik', 'kind': CostCenterKind.LOCATION, 'parent_code': '1'},
    {'code': '2', 'name': 'Smještaj', 'kind': CostCenterKind.GROUP, 'parent_code': None},
    {'code': '200', 'name': 'Sobe za iznajmljivanje', 'kind': CostCenterKind.LOCATION, 'parent_code': '2'},
    {'code': '210', 'name': 'Domaćinstvo i praonica', 'kind': CostCenterKind.LOCATION, 'parent_code': '2'},
    {'code': '3', 'name': 'Trgovina', 'kind': CostCenterKind.GROUP, 'parent_code': None},
    {'code': '300', 'name': 'Vinoteka', 'kind': CostCenterKind.LOCATION, 'parent_code': '3'},
    {'code': '7', 'name': 'Vozni park', 'kind': CostCenterKind.GROUP, 'parent_code': None},
    {'code': '9', 'name': 'Opće', 'kind': CostCenterKind.GROUP, 'parent_code': None},
    {'code': '900', 'name': 'Uprava', 'kind': CostCenterKind.OVERHEAD, 'parent_code': '9'},
    {'code': '910', 'name': 'Zgrada / zajednički objekt', 'kind': CostCenterKind.OVERHEAD, 'parent_code': '9'},
)

PRESETS = {
    'hospitality': HOSPITALITY_PRESET,
}


def cost_center_ref(cost_center: CostCenter | None) -> dict | None:
    if cost_center is None:
        return None
    return {
        'id': cost_center.pk,
        'code': cost_center.code,
        'name': cost_center.name,
        'kind': cost_center.kind,
        'is_active': cost_center.is_active,
        'is_bookable': cost_center.is_bookable,
        'parent_id': cost_center.parent_id,
    }


def _vehicle_ref(cost_center: CostCenter) -> dict | None:
    try:
        vehicle = cost_center.vehicle
    except (ObjectDoesNotExist, Vehicle.DoesNotExist):
        return None
    if vehicle is None:
        return None
    return {
        'id': vehicle.pk,
        'name': vehicle.name,
        'vin': vehicle.vin or '',
        'fixed_asset_id': vehicle.fixed_asset_id,
    }


def _fixed_asset_refs(cost_center: CostCenter) -> list[dict]:
    return [
        {'id': asset.pk, 'name': asset.name}
        for asset in cost_center.fixed_assets.all().order_by('name', 'pk')
    ]


def cost_center_dto(cost_center: CostCenter) -> dict:
    parent = cost_center.parent
    return {
        'id': cost_center.pk,
        'code': cost_center.code,
        'name': cost_center.name,
        'kind': cost_center.kind,
        'is_active': cost_center.is_active,
        'is_bookable': cost_center.is_bookable,
        'notes': cost_center.notes or '',
        'parent_id': cost_center.parent_id,
        'parent': cost_center_ref(parent) if parent is not None else None,
        'vehicle': _vehicle_ref(cost_center),
        'fixed_assets': _fixed_asset_refs(cost_center),
    }


def list_cost_centers(*, tenant, include_inactive: bool = False) -> dict:
    qs = (
        CostCenter.all_objects.filter(tenant=tenant)
        .select_related('parent', 'vehicle')
        .prefetch_related('fixed_assets')
    )
    if not include_inactive:
        qs = qs.filter(is_active=True)
    rows = [cost_center_dto(row) for row in qs.order_by('code')]
    return {'count': len(rows), 'results': rows}


def get_cost_center(*, tenant, cost_center_id: int) -> dict:
    return cost_center_dto(_load(tenant, cost_center_id))


def _load(tenant, cost_center_id: int) -> CostCenter:
    row = (
        CostCenter.all_objects.filter(tenant=tenant, pk=cost_center_id)
        .select_related('parent', 'vehicle')
        .prefetch_related('fixed_assets')
        .first()
    )
    if row is None:
        raise Http404()
    return row


def _apply_fields(row: CostCenter, data: dict) -> None:
    if 'code' in data:
        code = (data.get('code') or '').strip()
        if not code:
            raise ValidationError({'code': 'Šifra je obavezna.'})
        row.code = code
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            raise ValidationError({'name': 'Naziv je obavezan.'})
        row.name = name
    if 'kind' in data:
        kind = data.get('kind')
        if kind not in CostCenterKind.values:
            raise ValidationError({'kind': 'Nepoznata vrsta mjesta troška.'})
        row.kind = kind
    if 'notes' in data:
        row.notes = (data.get('notes') or '').strip()
    if 'is_active' in data:
        row.is_active = bool(data['is_active'])
    if 'parent_id' in data:
        parent_id = data.get('parent_id')
        if parent_id in (None, ''):
            row.parent = None
        else:
            parent = CostCenter.all_objects.filter(tenant=row.tenant, pk=parent_id).first()
            if parent is None:
                raise ValidationError({'parent_id': 'Nadređeno MT nije pronađeno.'})
            row.parent = parent


@transaction.atomic
def create_cost_center(*, tenant, data: dict) -> dict:
    row = CostCenter(tenant=tenant, kind=CostCenterKind.LOCATION)
    _apply_fields(row, data)
    if not row.code or not row.name:
        raise ValidationError({'code': 'Šifra i naziv su obavezni.'})
    row.full_clean()
    row.save()
    return cost_center_dto(row)


@transaction.atomic
def update_cost_center(*, tenant, cost_center_id: int, data: dict) -> dict:
    row = _load(tenant, cost_center_id)
    _apply_fields(row, data)
    row.full_clean()
    row.save()
    return cost_center_dto(row)


@transaction.atomic
def ensure_default_cost_centers(tenant, preset: str = 'hospitality') -> int:
    rows = PRESETS.get(preset)
    if rows is None:
        raise ValidationError({'preset': f'Nepoznat preset: {preset}'})
    created = 0
    by_code: dict[str, CostCenter] = {
        row.code: row
        for row in CostCenter.all_objects.filter(tenant=tenant)
    }
    for spec in rows:
        existing = by_code.get(spec['code'])
        if existing is not None:
            continue
        parent = by_code.get(spec['parent_code']) if spec['parent_code'] else None
        row = CostCenter(
            tenant=tenant,
            code=spec['code'],
            name=spec['name'],
            kind=spec['kind'],
            parent=parent,
            is_active=True,
        )
        row.full_clean()
        row.save()
        by_code[row.code] = row
        created += 1
    return created
