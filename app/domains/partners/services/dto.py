"""Partner MDM response DTOs (plain dicts)."""

from __future__ import annotations

from decimal import Decimal

from partners.models import Partner, PartnerBankAccount, PartnerContact


def _dec(value: Decimal | None) -> str:
    if value is None:
        return '0.00'
    return f'{value:.2f}'


def partner_dto(partner: Partner) -> dict:
    return {
        'id': partner.pk,
        'partner_code': partner.partner_code,
        'name': partner.name,
        'short_name': partner.short_name or '',
        'partner_type': partner.partner_type,
        'status': partner.status,
        'tax_number': partner.tax_number,
        'vat_number': partner.vat_number or '',
        'registration_number': partner.registration_number or '',
        'address': partner.address,
        'city': partner.city,
        'postal_code': partner.postal_code,
        'country': partner.country,
        'email': partner.email or '',
        'phone': partner.phone or '',
        'mobile': partner.mobile or '',
        'fax': partner.fax or '',
        'website': partner.website or '',
        'payment_terms': partner.payment_terms,
        'credit_limit': _dec(partner.credit_limit),
        'discount_percentage': _dec(partner.discount_percentage),
        'notes': partner.notes or '',
        'internal_notes': partner.internal_notes or '',
        'created_at': partner.created_at.isoformat() if partner.created_at else None,
        'updated_at': partner.updated_at.isoformat() if partner.updated_at else None,
    }


def partner_list_item_dto(partner: Partner) -> dict:
    return {
        'id': partner.pk,
        'partner_code': partner.partner_code,
        'name': partner.name,
        'short_name': partner.short_name or '',
        'partner_type': partner.partner_type,
        'status': partner.status,
        'tax_number': partner.tax_number,
        'city': partner.city,
        'country': partner.country,
        'email': partner.email or '',
        'phone': partner.phone or '',
    }


def contact_dto(contact: PartnerContact) -> dict:
    return {
        'id': contact.pk,
        'partner_id': contact.partner_id,
        'contact_type': contact.contact_type,
        'first_name': contact.first_name,
        'last_name': contact.last_name,
        'full_name': contact.full_name,
        'position': contact.position or '',
        'department': contact.department or '',
        'email': contact.email or '',
        'phone': contact.phone or '',
        'mobile': contact.mobile or '',
        'notes': contact.notes or '',
        'is_primary': contact.is_primary,
        'is_active': contact.is_active,
        'created_at': contact.created_at.isoformat() if contact.created_at else None,
    }


def bank_account_dto(account: PartnerBankAccount) -> dict:
    return {
        'id': account.pk,
        'partner_id': account.partner_id,
        'bank_name': account.bank_name,
        'bic': account.bic,
        'iban': account.iban,
        'currency': account.currency,
        'is_primary': account.is_primary,
        'is_active': account.is_active,
        'created_at': account.created_at.isoformat() if account.created_at else None,
    }
