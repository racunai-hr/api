from __future__ import annotations

from dataclasses import dataclass

from expenses.data.manual_supplier_map import MANUAL_SUPPLIER_MAP
from partners.models import Partner

_MAP_FIELDS = ('name', 'address', 'city', 'postal_code', 'country', 'email', 'phone')


@dataclass(frozen=True)
class ManualSupplierSeedResult:
    created: int
    skipped: int
    updated: int
    details: tuple[str, ...]


def _defaults(tax_number: str) -> dict:
    data = MANUAL_SUPPLIER_MAP[tax_number]
    return {
        'name': data['name'],
        'address': data.get('address', ''),
        'city': data.get('city', ''),
        'postal_code': data.get('postal_code', ''),
        'country': data.get('country', 'Croatia'),
        'country_code': data.get('country_code', 'HR'),
        'email': data.get('email', ''),
        'phone': data.get('phone', ''),
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


def seed_manual_suppliers(
    *,
    tenant,
    dry_run: bool = False,
    update_existing: bool = False,
) -> ManualSupplierSeedResult:
    created = 0
    skipped = 0
    updated = 0
    details: list[str] = []

    for tax_number in sorted(MANUAL_SUPPLIER_MAP):
        mapped = _defaults(tax_number)
        name = mapped['name']
        existing = Partner.all_objects.filter(tenant=tenant, tax_number=tax_number).first()

        if existing:
            _ensure_supplier_type(existing)
            if update_existing:
                update_fields: list[str] = []
                for field in _MAP_FIELDS:
                    new_value = mapped[field]
                    if getattr(existing, field) != new_value:
                        setattr(existing, field, new_value)
                        update_fields.append(field)
                if update_fields and not dry_run:
                    existing.save(update_fields=update_fields)
                if update_fields:
                    updated += 1
                    details.append(f'~ {name} ({tax_number}) — ažurirano')
                else:
                    skipped += 1
                    details.append(f'= {name} ({tax_number}) — već postoji')
            else:
                skipped += 1
                details.append(f'= {name} ({tax_number}) — već postoji')
            continue

        if dry_run:
            created += 1
            details.append(f'+ {name} ({tax_number}) — kreirat će se')
        else:
            Partner.all_objects.create(tenant=tenant, tax_number=tax_number, **mapped)
            created += 1
            details.append(f'+ {name} ({tax_number})')

    return ManualSupplierSeedResult(
        created=created,
        skipped=skipped,
        updated=updated,
        details=tuple(details),
    )
