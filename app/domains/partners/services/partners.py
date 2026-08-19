"""Partner MDM write/read services (ADR-0022)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone

from domains.partners.services.dto import (
    bank_account_dto,
    contact_dto,
    partner_dto,
    partner_list_item_dto,
)
from domains.reporting.documents.snapshot import isoformat, read_snapshot
from partners.models import Partner, PartnerBankAccount, PartnerContact
from shared.iban import normalize_iban


class PartnerTaxNumberConflict(Exception):
    """Duplicate tax_number within tenant."""


class PartnerIbanConflict(Exception):
    """Duplicate normalized IBAN within the same partner."""


@dataclass(frozen=True)
class PartnerListFilters:
    filter: str = 'active'  # active | all
    partner_type: str | None = None
    status: str | None = None
    search: str = ''
    page: int = 1
    page_size: int = 20


PARTNER_WRITE_FIELDS = (
    'name',
    'short_name',
    'partner_type',
    'status',
    'tax_number',
    'vat_number',
    'registration_number',
    'address',
    'city',
    'postal_code',
    'country',
    'email',
    'phone',
    'mobile',
    'fax',
    'website',
    'payment_terms',
    'credit_limit',
    'discount_percentage',
    'notes',
    'internal_notes',
)

CONTACT_WRITE_FIELDS = (
    'contact_type',
    'first_name',
    'last_name',
    'position',
    'department',
    'email',
    'phone',
    'mobile',
    'notes',
    'is_primary',
    'is_active',
)

BANK_ACCOUNT_WRITE_FIELDS = (
    'bank_name',
    'bic',
    'iban',
    'currency',
    'is_primary',
    'is_active',
)

CUSTOMER_TYPES = frozenset({'customer', 'both'})
SUPPLIER_TYPES = frozenset({'supplier', 'both'})


def parse_partner_list_filters(params) -> PartnerListFilters:
    raw_filter = (params.get('filter') or '').strip().lower()
    if raw_filter in ('', 'active', 'default'):
        list_filter = 'active'
    elif raw_filter in ('all', 'inactive'):
        list_filter = raw_filter
    else:
        raise ValueError('filter must be "all", "inactive", or omitted (default active)')

    partner_type = (params.get('partner_type') or '').strip() or None
    allowed_types = {'customer', 'supplier', 'both', 'other', 'customers', 'suppliers'}
    if partner_type is not None and partner_type not in allowed_types:
        raise ValueError('invalid partner_type')

    status = (params.get('status') or '').strip() or None
    if status and status not in dict(Partner.STATUS_CHOICES):
        raise ValueError('invalid status')

    search = (params.get('search') or params.get('q') or '').strip()

    try:
        page = max(1, int(params.get('page') or 1))
    except (TypeError, ValueError) as exc:
        raise ValueError('page must be an integer >= 1') from exc
    try:
        page_size = int(params.get('page_size') or 20)
    except (TypeError, ValueError) as exc:
        raise ValueError('page_size must be an integer') from exc
    page_size = max(1, min(page_size, 100))

    return PartnerListFilters(
        filter=list_filter,
        partner_type=partner_type,
        status=status,
        search=search,
        page=page,
        page_size=page_size,
    )


def _paginate(qs, page: int, page_size: int):
    total = qs.count()
    start = (page - 1) * page_size
    return total, list(qs[start : start + page_size])


def _get_partner(tenant, partner_id: int) -> Partner:
    try:
        return Partner.all_objects.get(tenant=tenant, pk=partner_id)
    except Partner.DoesNotExist as exc:
        raise Http404() from exc


def _money(value, field: str) -> Decimal:
    if value is None or value == '':
        return Decimal('0.00')
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f'{field} must be a decimal') from exc


def _apply_partner_fields(partner: Partner, data: dict, *, create: bool) -> None:
    for field in PARTNER_WRITE_FIELDS:
        if field not in data:
            if create and field in (
                'name',
                'partner_type',
                'tax_number',
                'address',
                'city',
                'postal_code',
            ):
                raise ValueError(f'{field} is required')
            continue
        value = data[field]
        if field in ('credit_limit', 'discount_percentage'):
            setattr(partner, field, _money(value, field))
        elif field == 'payment_terms':
            try:
                partner.payment_terms = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError('payment_terms must be an integer') from exc
        elif field == 'partner_type':
            if value not in dict(Partner.PARTNER_TYPES):
                raise ValueError('invalid partner_type')
            partner.partner_type = value
        elif field == 'status':
            if value not in dict(Partner.STATUS_CHOICES):
                raise ValueError('invalid status')
            partner.status = value
        else:
            setattr(partner, field, value if value is not None else '')


def list_partners(tenant, filters: PartnerListFilters) -> dict:
    with read_snapshot() as as_of:
        qs = Partner.all_objects.filter(tenant=tenant).order_by('name', 'id')

        if filters.status:
            qs = qs.filter(status=filters.status)
        elif filters.filter == 'active':
            qs = qs.filter(status='active')
        elif filters.filter == 'inactive':
            qs = qs.filter(status__in=('inactive', 'blocked'))

        if filters.partner_type == 'customers':
            qs = qs.filter(partner_type__in=CUSTOMER_TYPES)
        elif filters.partner_type == 'suppliers':
            qs = qs.filter(partner_type__in=SUPPLIER_TYPES)
        elif filters.partner_type:
            qs = qs.filter(partner_type=filters.partner_type)

        if filters.search:
            term = filters.search
            qs = qs.filter(
                Q(name__icontains=term)
                | Q(short_name__icontains=term)
                | Q(partner_code__icontains=term)
                | Q(tax_number__icontains=term)
                | Q(vat_number__icontains=term)
            )

        total, rows = _paginate(qs, filters.page, filters.page_size)
        return {
            'as_of': isoformat(as_of),
            'count': total,
            'page': filters.page,
            'page_size': filters.page_size,
            'results': [partner_list_item_dto(p) for p in rows],
        }


def get_partner(tenant, partner_id: int) -> dict:
    return partner_dto(_get_partner(tenant, partner_id))


def create_partner(tenant, data: dict, *, user=None) -> dict:
    partner = Partner(tenant=tenant, created_by=user if getattr(user, 'is_authenticated', False) else None)
    _apply_partner_fields(partner, data, create=True)
    if not partner.status:
        partner.status = 'active'
    try:
        with transaction.atomic():
            partner.save()
    except IntegrityError as exc:
        if 'unique_partner_tax_per_tenant' in str(exc) or 'tax_number' in str(exc).lower():
            raise PartnerTaxNumberConflict() from exc
        if 'unique_partner_code_per_tenant' in str(exc):
            raise ValueError('partner_code conflict') from exc
        raise
    return partner_dto(partner)


def update_partner(tenant, partner_id: int, data: dict) -> dict:
    partner = _get_partner(tenant, partner_id)
    _apply_partner_fields(partner, data, create=False)
    try:
        with transaction.atomic():
            partner.save()
    except IntegrityError as exc:
        if 'unique_partner_tax_per_tenant' in str(exc) or 'tax_number' in str(exc).lower():
            raise PartnerTaxNumberConflict() from exc
        if 'unique_partner_code_per_tenant' in str(exc):
            raise ValueError('partner_code conflict') from exc
        raise
    return partner_dto(partner)


def list_contacts(tenant, partner_id: int) -> dict:
    partner = _get_partner(tenant, partner_id)
    rows = list(PartnerContact.objects.filter(partner=partner).order_by('-is_primary', 'last_name', 'first_name', 'id'))
    return {
        'as_of': isoformat(timezone.now()),
        'count': len(rows),
        'results': [contact_dto(c) for c in rows],
    }


def _get_contact(tenant, partner_id: int, contact_id: int) -> PartnerContact:
    partner = _get_partner(tenant, partner_id)
    try:
        return PartnerContact.objects.get(partner=partner, pk=contact_id)
    except PartnerContact.DoesNotExist as exc:
        raise Http404() from exc


def _apply_contact_fields(contact: PartnerContact, data: dict, *, create: bool) -> None:
    for field in CONTACT_WRITE_FIELDS:
        if field not in data:
            if create and field in ('first_name', 'last_name'):
                raise ValueError(f'{field} is required')
            continue
        value = data[field]
        if field == 'contact_type':
            if value not in dict(PartnerContact.CONTACT_TYPES):
                raise ValueError('invalid contact_type')
            contact.contact_type = value
        elif field in ('is_primary', 'is_active'):
            contact.__dict__[field] = bool(value)
        else:
            setattr(contact, field, value if value is not None else '')


def create_contact(tenant, partner_id: int, data: dict) -> dict:
    partner = _get_partner(tenant, partner_id)
    contact = PartnerContact(partner=partner)
    _apply_contact_fields(contact, data, create=True)
    with transaction.atomic():
        if contact.is_primary:
            PartnerContact.objects.filter(partner=partner, is_primary=True).update(is_primary=False)
        contact.save()
    return contact_dto(contact)


def update_contact(tenant, partner_id: int, contact_id: int, data: dict) -> dict:
    contact = _get_contact(tenant, partner_id, contact_id)
    _apply_contact_fields(contact, data, create=False)
    with transaction.atomic():
        if data.get('is_primary') is True:
            PartnerContact.objects.filter(partner_id=contact.partner_id, is_primary=True).exclude(
                pk=contact.pk
            ).update(is_primary=False)
            contact.is_primary = True
        contact.save()
    return contact_dto(contact)


def delete_contact(tenant, partner_id: int, contact_id: int) -> None:
    contact = _get_contact(tenant, partner_id, contact_id)
    contact.delete()


def list_bank_accounts(tenant, partner_id: int) -> dict:
    partner = _get_partner(tenant, partner_id)
    rows = list(
        PartnerBankAccount.objects.filter(partner=partner).order_by('-is_primary', 'bank_name', 'id')
    )
    return {
        'as_of': isoformat(timezone.now()),
        'count': len(rows),
        'results': [bank_account_dto(a) for a in rows],
    }


def _get_bank_account(tenant, partner_id: int, account_id: int) -> PartnerBankAccount:
    partner = _get_partner(tenant, partner_id)
    try:
        return PartnerBankAccount.objects.get(partner=partner, pk=account_id)
    except PartnerBankAccount.DoesNotExist as exc:
        raise Http404() from exc


def _apply_bank_account_fields(account: PartnerBankAccount, data: dict, *, create: bool) -> None:
    for field in BANK_ACCOUNT_WRITE_FIELDS:
        if field not in data:
            if create and field in ('bank_name', 'bic', 'iban'):
                raise ValueError(f'{field} is required')
            continue
        value = data[field]
        if field == 'iban':
            account.iban = normalize_iban(str(value or ''))
            if not account.iban:
                raise ValueError('iban is required')
        elif field in ('is_primary', 'is_active'):
            setattr(account, field, bool(value))
        elif field == 'currency':
            account.currency = (str(value or 'EUR').upper())[:3]
        else:
            setattr(account, field, value if value is not None else '')


def create_bank_account(tenant, partner_id: int, data: dict) -> dict:
    partner = _get_partner(tenant, partner_id)
    account = PartnerBankAccount(partner=partner)
    _apply_bank_account_fields(account, data, create=True)
    try:
        with transaction.atomic():
            if account.is_primary:
                PartnerBankAccount.objects.filter(partner=partner, is_primary=True).update(is_primary=False)
            account.save()
    except IntegrityError as exc:
        if 'unique_partner_iban' in str(exc) or 'iban' in str(exc).lower():
            raise PartnerIbanConflict() from exc
        raise
    return bank_account_dto(account)


def update_bank_account(tenant, partner_id: int, account_id: int, data: dict) -> dict:
    account = _get_bank_account(tenant, partner_id, account_id)
    _apply_bank_account_fields(account, data, create=False)
    try:
        with transaction.atomic():
            if data.get('is_primary') is True:
                PartnerBankAccount.objects.filter(partner_id=account.partner_id, is_primary=True).exclude(
                    pk=account.pk
                ).update(is_primary=False)
                account.is_primary = True
            account.save()
    except IntegrityError as exc:
        if 'unique_partner_iban' in str(exc) or 'iban' in str(exc).lower():
            raise PartnerIbanConflict() from exc
        raise
    return bank_account_dto(account)


def delete_bank_account(tenant, partner_id: int, account_id: int) -> None:
    account = _get_bank_account(tenant, partner_id, account_id)
    account.delete()
