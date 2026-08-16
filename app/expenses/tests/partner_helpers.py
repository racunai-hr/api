"""Test helpers for expense/partner tests."""

from partners.models import Partner


def create_supplier_partner(*, tenant, **overrides) -> Partner:
    defaults = {
        'name': 'Dobavljač d.o.o.',
        'tax_number': '98765432109',
        'partner_type': 'supplier',
        'status': 'active',
        'address': 'Ulica 1',
        'city': 'Zagreb',
        'postal_code': '10000',
        'country': 'Hrvatska',
    }
    defaults.update(overrides)
    return Partner.all_objects.create(tenant=tenant, **defaults)
