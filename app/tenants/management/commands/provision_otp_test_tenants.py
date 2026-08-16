from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from banking.config import get_active_otp_provider
from banking.otp.psu import (
    ALL_OTP_TEST_TENANTS,
    DEFAULT_OTP_TEST_TENANTS,
    otp_test_psu_by_slug,
)
from banking.provider_models import BankConnection
from integrations.constants import IntegrationEnvironment, IntegrationProvider, IntegrationType
from integrations.models import IntegrationConfig
from payments.models import BankAccount
from settings.models import CompanySettings, TaxOffice, TaxRate
from tenants.models import Tenant, TenantMembership


TAX_RATES = [
    ('PDV 25%', '25.00', True),
    ('PDV 13%', '13.00', False),
    ('PDV 5%', '5.00', False),
    ('Oslobođeno', '0.00', False),
]


class Command(BaseCommand):
    help = 'Provisionira OTP sandbox test tenant(e): chart, company, bank account, connection.'

    def add_arguments(self, parser):
        parser.add_argument('--admin-user', type=str, required=True, help='Username za TenantMembership owner')
        parser.add_argument('--dry-run', action='store_true', help='Ispiši plan bez DB promjena')
        parser.add_argument('--tenant', type=str, help='Jedan tenant slug')
        parser.add_argument('--all', action='store_true', help='Svih 8 OTP test tenant-a')
        parser.add_argument('--reset', action='store_true', help='reset_otp_test_tenant prije provisiona')

    def handle(self, *args, **options):
        slugs = self._resolve_slugs(options)
        admin_user = options['admin_user']

        if options['dry_run']:
            psu_map = otp_test_psu_by_slug()
            for slug in slugs:
                psu = psu_map.get(slug, '?')
                self.stdout.write(f'[dry-run] tenant={slug} psu={psu} admin={admin_user}')
            return

        try:
            user = User.objects.get(username=admin_user)
        except User.DoesNotExist as exc:
            raise CommandError(f'Korisnik {admin_user} ne postoji.') from exc

        provider = get_active_otp_provider()
        if provider is None:
            raise CommandError('OTP provider nije konfiguriran (otp_sandbox).')

        for slug in slugs:
            if options['reset']:
                call_command('reset_otp_test_tenant', slug)
            self._provision_tenant(slug, user, provider)

        call_command('ensure_banking_beat_schedule')
        self.stdout.write(self.style.SUCCESS(f'Provisionirano {len(slugs)} tenant(a).'))

    def _resolve_slugs(self, options) -> tuple[str, ...]:
        if options['tenant']:
            return (options['tenant'],)
        if options['all']:
            return ALL_OTP_TEST_TENANTS
        return DEFAULT_OTP_TEST_TENANTS

    @transaction.atomic
    def _provision_tenant(self, slug: str, user: User, provider) -> None:
        psu = otp_test_psu_by_slug().get(slug)
        if psu is None:
            raise CommandError(f'Nepoznat OTP test slug: {slug}')

        display_name = slug.replace('-', ' ').title()
        tenant, _ = Tenant.objects.get_or_create(
            slug=slug,
            defaults={'name': display_name, 'is_active': True},
        )
        tenant.name = display_name
        tenant.is_active = True
        tenant.save(update_fields=['name', 'is_active'])

        company_defaults = {
            'company_name': display_name,
            'company_address': 'OTP Sandbox Test\n10000 Zagreb',
            'street': 'OTP Sandbox Test',
            'house_number': '',
            'postal_code': '10000',
            'city': 'Zagreb',
            'country': 'HR',
            'company_phone': '+385 1 000 0000',
            'company_email': f'{slug}@otp-test.racunai.hr',
            'company_website': '',
            'registration_number': psu,
        }
        company, _ = CompanySettings.all_objects.get_or_create(
            tenant=tenant,
            defaults=company_defaults,
        )
        for key, value in company_defaults.items():
            setattr(company, key, value)
        tax_office = TaxOffice.objects.filter(code='3566').first()
        if tax_office:
            company.tax_office = tax_office
        company.save()

        default_tax = None
        for name, rate, is_default in TAX_RATES:
            tax, _ = TaxRate.all_objects.get_or_create(
                tenant=tenant,
                name=name,
                defaults={'rate': rate, 'is_default': is_default, 'is_active': True},
            )
            tax.rate = rate
            tax.is_default = is_default
            tax.is_active = True
            tax.save()
            if is_default:
                default_tax = tax
        if default_tax:
            company.default_tax_rate = default_tax
            company.save(update_fields=['default_tax_rate'])

        call_command('setup_accounting', tenant=slug)

        TenantMembership.objects.update_or_create(
            user=user,
            tenant=tenant,
            defaults={'role': 'owner', 'is_default': False},
        )

        IntegrationConfig.all_objects.update_or_create(
            tenant=tenant,
            integration_type=IntegrationType.PAYMENT,
            provider=IntegrationProvider.OTP,
            environment=IntegrationEnvironment.SANDBOX,
            defaults={
                'is_active': True,
                'display_name': 'OTP banka (sandbox)',
            },
        )

        bank_account, _ = BankAccount.all_objects.get_or_create(
            tenant=tenant,
            account_number=f'pending-{slug}',
            defaults={
                'account_name': 'OTP račun',
                'bank_name': provider.name,
                'iban': None,
                'currency': 'EUR',
                'status': 'pending_discovery',
            },
        )
        bank_account.status = 'pending_discovery'
        bank_account.iban = None
        bank_account.external_account_id = ''
        bank_account.provider_connection = None
        bank_account.discovered_at = None
        bank_account.bank_name = provider.name
        bank_account.save()

        BankConnection.all_objects.update_or_create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=bank_account,
            defaults={'status': 'pending'},
        )

        self.stdout.write(self.style.SUCCESS(f'  ✔ {slug} (PSU: {psu})'))
