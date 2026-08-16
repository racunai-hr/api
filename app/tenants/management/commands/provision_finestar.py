from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from accounting.models import ChartOfAccounts
from expenses.models import ExpenseCategory
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice, TaxRate
from tenants.models import Tenant


FINE_STAR = {
    'company_name': 'Fine Star d.o.o.',
    'company_address': 'Bana Josipa Jelačića 58\n22000 Šibenik',
    'street': 'Bana Josipa Jelačića',
    'house_number': '58',
    'postal_code': '22000',
    'city': 'Šibenik',
    'country': 'HR',
    'company_phone': '+385 22 000 000',
    'company_email': 'avrcan@finestar.hr',
    'company_website': 'https://finestar.hr',
    'vat_number': '36619131370',
    'registration_number': '080885494',
}

TAX_RATES = [
    ('PDV 25%', '25.00', True),
    ('PDV 13%', '13.00', False),
    ('PDV 5%', '5.00', False),
    ('Oslobođeno', '0.00', False),
]

EXPENSE_CATEGORIES = [
    ('Hosting i infrastruktura', '4120'),
    ('Software licence', '4120'),
    ('Telekomunikacije', '4120'),
    ('Uredski materijal', '4120'),
    ('Ostalo', '4120'),
]

RESPONSIBLE_PERSON = {
    'title': 'director',
    'first_name': 'Toni',
    'last_name': 'Šupe',
    'email': 'avrcan@finestar.hr',
}


class Command(BaseCommand):
    help = 'Inicijalizira Fine Star d.o.o. tenant (postavke, PDV, kategorije, SUPER, računovodstvo)'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, default='finestar')

    @transaction.atomic
    def handle(self, *args, **options):
        slug = options['tenant']
        tenant, _ = Tenant.objects.get_or_create(
            slug=slug,
            defaults={'name': FINE_STAR['company_name'], 'is_active': True},
        )
        tenant.name = FINE_STAR['company_name']
        tenant.is_active = True
        tenant.save(update_fields=['name', 'is_active'])

        company, _ = CompanySettings.all_objects.get_or_create(
            tenant=tenant,
            defaults=FINE_STAR,
        )
        for key, value in FINE_STAR.items():
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

        if not company.responsible_persons.exists():
            ResponsiblePerson.objects.create(
                company_settings=company,
                is_primary=True,
                is_active=True,
                **RESPONSIBLE_PERSON,
            )

        call_command('setup_accounting', tenant=slug)

        for cat_name, account_code in EXPENSE_CATEGORIES:
            account = ChartOfAccounts.all_objects.filter(
                tenant=tenant,
                account_code=account_code,
            ).first()
            ExpenseCategory.all_objects.update_or_create(
                tenant=tenant,
                name=cat_name,
                defaults={
                    'default_account': account,
                    'is_active': True,
                },
            )

        self.stdout.write(
            'DIRECT konfiguracija: provjerite Admin → DIRECT eRačun konfiguracije '
            f'(https://{slug}.racunai.hr/admin/fiscal_gateway/directtenantconfig/)'
        )

        self._ensure_celery_beat_schedule()
        self._ensure_integration_config(tenant)

        self.stdout.write(self.style.SUCCESS(f'Fine Star tenant {slug} provisioniran'))

    def _ensure_integration_config(self, tenant):
        from integrations.constants import (
            IntegrationEnvironment,
            IntegrationProvider,
            IntegrationType,
        )
        from integrations.models import IntegrationConfig
        from fiscal_gateway.models import DirectTenantConfig

        if not DirectTenantConfig.all_objects.filter(tenant=tenant, is_active=True).exists():
            return

        IntegrationConfig.all_objects.update_or_create(
            tenant=tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            environment=IntegrationEnvironment.PRODUCTION,
            defaults={
                'is_active': True,
                'display_name': 'DIRECT AS4 (produkcija)',
            },
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

    def _ensure_celery_beat_schedule(self):
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='*/15',
            hour='*',
            day_of_week='*',
            day_of_month='*',
            month_of_year='*',
            timezone='Europe/Zagreb',
        )
        PeriodicTask.objects.update_or_create(
            name='integrations.sync_all_eracun_tenants',
            defaults={
                'task': 'integrations.sync_all_eracun_tenants',
                'crontab': schedule,
                'enabled': True,
            },
        )
        PeriodicTask.objects.update_or_create(
            name='banking.sync_all_active_connections',
            defaults={
                'task': 'banking.sync_all_active_connections',
                'crontab': schedule,
                'enabled': True,
            },
        )
