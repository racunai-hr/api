"""Canonical MDM ensure for Toni Šupe (Fine Star director / loan creditor).

No banking/finance reconcile or posting side effects — Partner + IBAN only.
"""

from __future__ import annotations

from django.db import transaction

from partners.models import Partner, PartnerBankAccount
from settings.models import CompanySettings, ResponsiblePerson
from shared.countries import country_display_name
from shared.iban import normalize_iban
from shared.oib import is_valid_oib, normalize_oib

TONI_OIB = '63281973348'
TONI_NAME = 'Toni Šupe'
TONI_SHORT = 'Toni'
TONI_NOTES = 'Direktor Fine Star; vjerovnik po pozajmicama (2309).'
TONI_ADDRESS = 'Šibenskih vatrogasaca 10'
TONI_CITY = 'Šibenik'
TONI_POSTAL = '22000'
TONI_EMAIL_CANONICAL = 'tsupe@finestar.hr'

IBAN_PRIMARY = 'LT743250078044004209'
IBAN_SECONDARY = 'HR5324070001024070003'


def ensure_partner_toni_supe(*, tenant, user=None) -> Partner:
    """Idempotent Partner Toni + LT74 primary / HR53 secondary IBANs. Never deletes accounts."""
    oib = normalize_oib(TONI_OIB)
    if not is_valid_oib(oib):
        raise ValueError(f'OIB {TONI_OIB} nije valjan HR OIB.')

    iban_lt = normalize_iban(IBAN_PRIMARY)
    iban_hr = normalize_iban(IBAN_SECONDARY)

    with transaction.atomic():
        partner = Partner.all_objects.filter(tenant=tenant, tax_number=oib).first()
        if partner is None:
            partner = Partner.all_objects.filter(tenant=tenant, name=TONI_NAME).first()

        if partner is None:
            partner = Partner.all_objects.create(
                tenant=tenant,
                name=TONI_NAME,
                short_name=TONI_SHORT,
                partner_type='other',
                status='active',
                tax_number=oib,
                address=TONI_ADDRESS,
                city=TONI_CITY,
                postal_code=TONI_POSTAL,
                country_code='HR',
                country=country_display_name('HR'),
                email=TONI_EMAIL_CANONICAL,
                notes=TONI_NOTES,
                created_by=user if getattr(user, 'is_authenticated', False) else None,
            )
        else:
            updates: list[str] = []
            if partner.tax_number != oib:
                partner.tax_number = oib
                updates.append('tax_number')
            if not partner.address:
                partner.address = TONI_ADDRESS
                updates.append('address')
            if not partner.city:
                partner.city = TONI_CITY
                updates.append('city')
            if not partner.postal_code:
                partner.postal_code = TONI_POSTAL
                updates.append('postal_code')
            if partner.partner_type != 'other':
                partner.partner_type = 'other'
                updates.append('partner_type')
            if partner.status != 'active':
                partner.status = 'active'
                updates.append('status')
            if partner.country_code != 'HR':
                partner.country_code = 'HR'
                partner.country = country_display_name('HR')
                updates.extend(['country_code', 'country'])
            if not partner.notes:
                partner.notes = TONI_NOTES
                updates.append('notes')
            if not partner.email:
                partner.email = TONI_EMAIL_CANONICAL
                updates.append('email')
            if updates:
                partner.save(update_fields=[*updates, 'updated_at'])

        def _ensure_iban(iban: str) -> PartnerBankAccount:
            account = PartnerBankAccount.objects.filter(partner=partner, iban=iban).first()
            if account is None:
                account = PartnerBankAccount.objects.create(
                    partner=partner,
                    iban=iban,
                    currency='EUR',
                    is_primary=False,
                    is_active=True,
                )
            return account

        lt_account = _ensure_iban(iban_lt)
        hr_account = _ensure_iban(iban_hr)

        PartnerBankAccount.objects.filter(partner=partner, is_primary=True).exclude(
            pk=lt_account.pk
        ).update(is_primary=False)

        if not lt_account.is_primary:
            lt_account.is_primary = True
            lt_account.save(update_fields=['is_primary'])
        if hr_account.is_primary:
            hr_account.is_primary = False
            hr_account.save(update_fields=['is_primary'])

        return partner


def ensure_finestar_director_toni_supe(*, tenant) -> ResponsiblePerson | None:
    """Promote Toni director on this tenant's CompanySettings only. Never deletes; never touches other tenants."""
    company = CompanySettings.all_objects.filter(tenant=tenant).first()
    if company is None:
        return None

    with transaction.atomic():
        toni_qs = ResponsiblePerson.objects.filter(
            company_settings=company,
            title='director',
            first_name='Toni',
            last_name='Šupe',
        ).order_by('id')
        rows = list(toni_qs)
        if not rows:
            person = ResponsiblePerson.objects.create(
                company_settings=company,
                title='director',
                first_name='Toni',
                last_name='Šupe',
                email=TONI_EMAIL_CANONICAL,
                is_primary=True,
                is_active=True,
            )
            return person

        preferred = next(
            (p for p in rows if (p.email or '').lower() == TONI_EMAIL_CANONICAL),
            rows[0],
        )
        for person in rows:
            if person.pk == preferred.pk:
                fields: list[str] = []
                if not person.is_primary:
                    person.is_primary = True
                    fields.append('is_primary')
                if not person.is_active:
                    person.is_active = True
                    fields.append('is_active')
                if fields:
                    person.save(update_fields=[*fields, 'updated_at'])
            else:
                fields = []
                if person.is_primary:
                    person.is_primary = False
                    fields.append('is_primary')
                if person.is_active:
                    person.is_active = False
                    fields.append('is_active')
                if fields:
                    person.save(update_fields=[*fields, 'updated_at'])
        return preferred
