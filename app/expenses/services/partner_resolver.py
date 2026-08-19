from __future__ import annotations

from partners.models import Partner


def resolve_partner(*, tenant, oib: str, name: str = '', partner_type: str = 'supplier') -> Partner:
    """Pronađi ili kreiraj partnera po OIB-u (TD-001 kanonski MDM)."""
    oib = (oib or '').strip()
    if not oib:
        raise ValueError('Nedostaje OIB dobavljača')

    partner = Partner.all_objects.filter(tenant=tenant, tax_number=oib).first()
    if partner:
        if partner_type == 'supplier' and partner.partner_type == 'customer':
            partner.partner_type = 'both'
            partner.save(update_fields=['partner_type'])
        elif partner_type == 'supplier' and partner.partner_type not in ('supplier', 'both'):
            partner.partner_type = 'supplier'
            partner.save(update_fields=['partner_type'])
        if name and partner.name != name:
            partner.name = name
            partner.save(update_fields=['name'])
        return partner

    display_name = name or f'OIB {oib}'
    return Partner.all_objects.create(
        tenant=tenant,
        tax_number=oib,
        name=display_name,
        partner_type=partner_type,
        address='',
        city='',
        postal_code='',
        country_code='HR',
        country='Hrvatska',
        status='active',
    )
