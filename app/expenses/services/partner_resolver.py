from __future__ import annotations

from partners.models import Partner


def resolve_partner(*, tenant, oib: str, name: str = '', partner_type: str = 'supplier') -> Partner:
    """Pronađi ili kreiraj partnera po OIB-u (TD-001 kanonski MDM)."""
    oib = (oib or '').strip()
    if not oib:
        raise ValueError('Nedostaje OIB dobavljača')

    partner = Partner.all_objects.filter(tenant=tenant, tax_number=oib).first()
    if partner:
        next_type = partner.partner_type
        # `other` is a residual / natural-person identity (ADR-0026). Do not recast it
        # just because an invoice matched the same OIB.
        if partner.partner_type != 'other':
            if partner_type in ('supplier', 'customer') and partner.partner_type in ('supplier', 'customer'):
                if partner.partner_type != partner_type:
                    next_type = 'both'
            elif partner_type == 'supplier' and partner.partner_type not in ('supplier', 'both'):
                next_type = 'supplier'
            elif partner_type == 'customer' and partner.partner_type not in ('customer', 'both'):
                next_type = 'customer'
        updates = []
        if next_type != partner.partner_type:
            partner.partner_type = next_type
            updates.append('partner_type')
        # MDM name is canonical. Fill a blank; never overwrite an existing name from
        # invoice/import payload (test UBL reused a real OIB as "Test Kupac d.o.o.").
        if name and not (partner.name or '').strip():
            partner.name = name
            updates.append('name')
        if updates:
            partner.save(update_fields=updates)
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
