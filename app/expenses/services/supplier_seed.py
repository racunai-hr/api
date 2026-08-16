from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from expenses.data.f1_supplier_map import F1_SUPPLIER_MAP
from expenses.services.f1_supplier_extract import extract_supplier_oibs_from_paths, resolve_csv_paths
from partners.models import Partner

_MAP_FIELDS = ('name', 'address', 'city', 'postal_code', 'email', 'phone')


@dataclass(frozen=True)
class SupplierSeedResult:
    created: int
    skipped: int
    updated: int
    missing_in_map: tuple[str, ...]
    details: tuple[str, ...]


def _map_defaults(oib: str) -> dict:
    data = F1_SUPPLIER_MAP[oib]
    return {
        'name': data['name'],
        'address': data.get('address', ''),
        'city': data.get('city', ''),
        'postal_code': data.get('postal_code', ''),
        'email': data.get('email', ''),
        'phone': data.get('phone', ''),
        'country': data.get('country', 'Croatia'),
        'partner_type': 'supplier',
        'status': 'active',
    }


def _ensure_supplier_type(partner: Partner) -> None:
    if partner.partner_type == 'customer':
        partner.partner_type = 'both'
        partner.save(update_fields=['partner_type'])
    elif partner.partner_type not in ('supplier', 'both'):
        partner.partner_type = 'supplier'
        partner.save(update_fields=['partner_type'])


def seed_suppliers_from_f1(
    *,
    tenant,
    paths: list[str | Path],
    dry_run: bool = False,
    update_existing: bool = False,
) -> SupplierSeedResult:
    oib_counts = extract_supplier_oibs_from_paths(paths)
    created = 0
    skipped = 0
    updated = 0
    missing_in_map: list[str] = []
    details: list[str] = []

    for oib in sorted(oib_counts):
        existing = Partner.all_objects.filter(tenant=tenant, tax_number=oib).first()

        if oib not in F1_SUPPLIER_MAP:
            missing_in_map.append(oib)
            details.append(f'? Nepoznat OIB {oib} ({oib_counts[oib]} računa) — nema u mapi')
            continue

        mapped = _map_defaults(oib)
        name = mapped['name']

        if existing:
            _ensure_supplier_type(existing)
            if update_existing:
                update_fields: list[str] = []
                for field in _MAP_FIELDS + ('country',):
                    new_value = mapped[field]
                    if getattr(existing, field) != new_value:
                        setattr(existing, field, new_value)
                        update_fields.append(field)
                if update_fields and not dry_run:
                    existing.save(update_fields=update_fields)
                if update_fields:
                    updated += 1
                    details.append(f'~ {name} ({oib}) — ažurirano')
                else:
                    skipped += 1
                    details.append(f'= {name} ({oib}) — već postoji')
            else:
                skipped += 1
                details.append(f'= {name} ({oib}) — već postoji')
            continue

        if dry_run:
            created += 1
            details.append(f'+ {name} ({oib}) — kreirat će se')
        else:
            Partner.all_objects.create(tenant=tenant, tax_number=oib, **mapped)
            created += 1
            details.append(f'+ {name} ({oib})')

    return SupplierSeedResult(
        created=created,
        skipped=skipped,
        updated=updated,
        missing_in_map=tuple(missing_in_map),
        details=tuple(details),
    )


def seed_suppliers_from_f1_path(
    *,
    tenant,
    path: str | Path,
    dry_run: bool = False,
    update_existing: bool = False,
) -> SupplierSeedResult:
    csv_paths = resolve_csv_paths(path)
    return seed_suppliers_from_f1(
        tenant=tenant,
        paths=csv_paths,
        dry_run=dry_run,
        update_existing=update_existing,
    )
