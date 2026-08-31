"""Generic tenant provisioning. Command is a thin adapter over this service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import ChartOfAccounts, PostingRule, RRIFChartEntry, VATPeriod
from accounting.services.rrif_import import import_rrif_chart
from accounting.services.vat import get_or_create_vat_period
from settings.models import CompanySettings, TaxOffice, TaxRate, VatRegistrationStatus
from settings.vat_profile import validate_company_vat_profile
from tenants.models import Tenant, TenantMembership, validate_tenant_slug


TAX_RATES_REGISTERED = (
    ('PDV 25%', '25.00', True),
    ('PDV 13%', '13.00', False),
    ('PDV 5%', '5.00', False),
    ('Oslobođeno', '0.00', False),
)
TAX_RATES_OUTSIDE_VAT = (
    ('Oslobođeno', '0.00', True),
    ('PDV 25%', '25.00', False),
    ('PDV 13%', '13.00', False),
    ('PDV 5%', '5.00', False),
)

IDENTITY_FIELDS = ('vat_number', 'vat_id', 'vat_registration_status')
SOFT_FIELDS = (
    'company_name',
    'street',
    'house_number',
    'postal_code',
    'city',
    'company_email',
    'company_phone',
    'company_website',
    'tax_office_id',
)


class TenantProvisionError(ValidationError):
    """Provisioning rejected before or during write."""


class TenantProvisionConflict(TenantProvisionError):
    """Existing tenant identity/parameters conflict with the requested spec."""


@dataclass(frozen=True)
class TenantProvisionSpec:
    slug: str
    name: str
    oib: str
    street: str
    house_number: str
    postal_code: str
    city: str
    email: str
    vat_status: str
    vat_id: str = ''
    phone: str = ''
    website: str = ''
    tax_office_code: str = ''
    owner_username: str = ''
    vat_year: int | None = None
    vat_month: int | None = None


@dataclass(frozen=True)
class TenantProvisionPlan:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    actions: tuple[str, ...]
    oib: str = ''
    vat_id: str = ''

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class TenantProvisionResult:
    created: bool
    changed: tuple[str, ...]
    warnings: tuple[str, ...]
    accounts: int
    posting_rules: int
    vat_period_id: int | None


def validate_provision(
    spec: TenantProvisionSpec,
    *,
    update_existing: bool = False,
) -> TenantProvisionPlan:
    """Read-only validation. Never writes."""
    errors: list[str] = []
    warnings: list[str] = []
    actions: list[str] = []
    oib = ''
    vat_id = ''

    slug = (spec.slug or '').strip()
    if not slug:
        errors.append('Slug je obavezan.')
    else:
        try:
            validate_tenant_slug(slug)
        except ValidationError as exc:
            errors.extend(_flatten_validation(exc))

    try:
        oib, vat_id = validate_company_vat_profile(
            oib=spec.oib,
            vat_id=spec.vat_id,
            status=spec.vat_status,
        )
    except ValidationError as exc:
        errors.extend(_flatten_validation(exc))

    tenant = Tenant.objects.filter(slug=slug).first() if slug else None
    if tenant is None:
        actions.append(f'Kreirati tenant {slug}.')
    else:
        actions.append(f'Tenant {slug} već postoji.')

    if oib:
        clash = (
            CompanySettings.all_objects.filter(vat_number=oib)
            .exclude(tenant=tenant)
            .select_related('tenant')
            .first()
        )
        if clash is not None:
            errors.append(f'OIB {oib} je već vezan za tenant {clash.tenant.slug}.')

    tax_office = None
    if spec.tax_office_code:
        tax_office = TaxOffice.objects.filter(code=spec.tax_office_code).first()
        if tax_office is None:
            errors.append(f'Porezni ured {spec.tax_office_code} nije u šifrarniku.')
    else:
        warnings.append('Porezni ured nije zadan — CompanySettings.tax_office ostaje prazan.')

    owner = None
    if spec.owner_username:
        owner = User.objects.filter(username=spec.owner_username).first()
        if owner is None:
            errors.append(f'Korisnik {spec.owner_username} ne postoji.')

    if tenant is not None:
        company = CompanySettings.all_objects.filter(tenant=tenant).first()
        if company is not None:
            expected = _expected_company_values(spec, oib, vat_id, tax_office)
            identity_diff = _field_diff(company, expected, IDENTITY_FIELDS)
            soft_diff = _field_diff(company, expected, SOFT_FIELDS)
            if identity_diff or soft_diff:
                detail = ', '.join(identity_diff + soft_diff)
                if update_existing:
                    actions.append(f'Ažurirati postojeća polja: {detail}.')
                else:
                    errors.append(
                        'Postojeći tenant ima drugačije podatke (koristi --update-existing): '
                        + detail
                    )
            else:
                actions.append('Identitet i parametri identični — no-op.')

    if spec.owner_username and owner is not None:
        actions.append(f'Članstvo owner: {spec.owner_username}.')
    if spec.vat_year and spec.vat_month:
        actions.append(f'PDV razdoblje {spec.vat_year}-{spec.vat_month:02d}.')
    elif spec.vat_year or spec.vat_month:
        errors.append('vat-year i vat-month moraju biti zadani zajedno.')

    return TenantProvisionPlan(
        errors=tuple(errors),
        warnings=tuple(warnings),
        actions=tuple(actions),
        oib=oib,
        vat_id=vat_id,
    )


def provision_tenant(spec: TenantProvisionSpec, *, update_existing: bool = False) -> TenantProvisionResult:
    plan = validate_provision(spec, update_existing=update_existing)
    if plan.errors:
        raise TenantProvisionConflict(list(plan.errors))

    with transaction.atomic():
        return _provision_in_transaction(spec, plan, update_existing=update_existing)


def _provision_in_transaction(
    spec: TenantProvisionSpec,
    plan: TenantProvisionPlan,
    *,
    update_existing: bool,
) -> TenantProvisionResult:
    if not RRIFChartEntry.objects.exists():
        import_rrif_chart()

    tenant, tenant_created = Tenant.objects.get_or_create(
        slug=spec.slug,
        defaults={'name': spec.name, 'is_active': True},
    )
    changed: list[str] = []
    if tenant_created:
        changed.append('tenant')
    elif tenant.name != spec.name and update_existing:
        tenant.name = spec.name
        tenant.is_active = True
        tenant.save(update_fields=['name', 'is_active'])
        changed.append('tenant.name')

    tax_office = None
    if spec.tax_office_code:
        tax_office = TaxOffice.objects.filter(code=spec.tax_office_code).first()

    expected = _expected_company_values(spec, plan.oib, plan.vat_id, tax_office)
    company = CompanySettings.all_objects.filter(tenant=tenant).first()
    if company is None:
        company = CompanySettings.all_objects.create(tenant=tenant, **expected)
        changed.append('company')
    else:
        identity_diff = _field_diff(company, expected, IDENTITY_FIELDS)
        soft_diff = _field_diff(company, expected, SOFT_FIELDS)
        if identity_diff or soft_diff:
            if not update_existing:
                raise TenantProvisionConflict(
                    ['Postojeći tenant ima drugačije podatke.'] + identity_diff + soft_diff
                )
            for field, value in expected.items():
                setattr(company, field, value)
            company.save()
            changed.extend(identity_diff + soft_diff)

    _ensure_tax_rates(tenant, company, spec.vat_status)

    vat_period_id = None
    year, month = _period_tuple(spec)
    if year and month:
        period = get_or_create_vat_period(tenant, year, month)
        vat_period_id = period.pk

    if spec.owner_username:
        user = User.objects.get(username=spec.owner_username)
        membership, created_membership = TenantMembership.objects.update_or_create(
            user=user,
            tenant=tenant,
            defaults={'role': 'owner'},
        )
        if created_membership:
            changed.append('membership')

    accounts = ChartOfAccounts.all_objects.filter(tenant=tenant).count()
    posting_rules = PostingRule.all_objects.filter(tenant=tenant).count()
    return TenantProvisionResult(
        created=tenant_created,
        changed=tuple(changed),
        warnings=plan.warnings,
        accounts=accounts,
        posting_rules=posting_rules,
        vat_period_id=vat_period_id,
    )


def _expected_company_values(spec: TenantProvisionSpec, oib: str, vat_id: str, tax_office) -> dict:
    address = f'{spec.street} {spec.house_number}\n{spec.postal_code} {spec.city}'.strip()
    return {
        'company_name': spec.name,
        'company_address': address,
        'street': spec.street,
        'house_number': spec.house_number,
        'postal_code': spec.postal_code,
        'city': spec.city,
        'country': 'HR',
        'company_phone': spec.phone or '',
        'company_email': spec.email,
        'company_website': spec.website or '',
        'vat_number': oib,
        'vat_id': vat_id,
        'vat_registration_status': spec.vat_status,
        'tax_office': tax_office,
    }


def _field_diff(company, expected: dict, fields: tuple[str, ...]) -> list[str]:
    diffs = []
    for field in fields:
        current = getattr(company, field)
        wanted = expected.get(field)
        if field == 'tax_office_id':
            wanted = expected['tax_office'].pk if expected.get('tax_office') else None
        if (current or '') != (wanted or '') and current != wanted:
            diffs.append(field)
    return diffs


def _ensure_tax_rates(tenant, company, vat_status: str) -> None:
    rates = TAX_RATES_REGISTERED if vat_status == VatRegistrationStatus.REGISTERED else TAX_RATES_OUTSIDE_VAT
    default_tax = None
    for name, rate, is_default in rates:
        tax, _ = TaxRate.all_objects.get_or_create(
            tenant=tenant,
            name=name,
            defaults={'rate': rate, 'is_default': is_default, 'is_active': True},
        )
        if is_default:
            default_tax = tax
    if default_tax and company.default_tax_rate_id != default_tax.pk:
        company.default_tax_rate = default_tax
        company.save(update_fields=['default_tax_rate'])


def _period_tuple(spec: TenantProvisionSpec) -> tuple[int | None, int | None]:
    if spec.vat_year and spec.vat_month:
        return spec.vat_year, spec.vat_month
    if spec.vat_year or spec.vat_month:
        return None, None
    now = timezone.localtime(timezone.now()) if timezone.is_aware(timezone.now()) else datetime.now()
    return now.year, now.month


def _flatten_validation(exc: ValidationError) -> list[str]:
    if hasattr(exc, 'message_dict'):
        messages = []
        for field, items in exc.message_dict.items():
            for item in items:
                messages.append(f'{field}: {item}' if field != '__all__' else item)
        return messages
    return list(exc.messages)
