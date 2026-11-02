from django.core.management.base import BaseCommand, CommandError

from settings.models import VatRegistrationStatus
from tenants.services.provisioning import (
    TenantProvisionConflict,
    TenantProvisionError,
    TenantProvisionSpec,
    provision_tenant,
    validate_provision,
)


class Command(BaseCommand):
    help = 'Provisionira tenant: profil tvrtke, PDV status, stope, članstvo, PDV razdoblje.'

    def add_arguments(self, parser):
        parser.add_argument('--slug', required=True)
        parser.add_argument('--name', required=True)
        parser.add_argument('--oib', required=True)
        parser.add_argument('--vat-id', default='', dest='vat_id')
        parser.add_argument(
            '--vat-status',
            required=True,
            choices=[choice[0] for choice in VatRegistrationStatus.choices],
        )
        parser.add_argument('--street', required=True)
        parser.add_argument('--house-number', required=True, dest='house_number')
        parser.add_argument('--postal-code', required=True, dest='postal_code')
        parser.add_argument('--city', required=True)
        parser.add_argument('--email', required=True)
        parser.add_argument('--phone', default='')
        parser.add_argument('--website', default='')
        parser.add_argument('--tax-office-code', default='', dest='tax_office_code')
        parser.add_argument('--owner-user', default='', dest='owner_username')
        parser.add_argument('--vat-year', type=int, default=None, dest='vat_year')
        parser.add_argument('--vat-month', type=int, default=None, dest='vat_month')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--update-existing', action='store_true')

    def handle(self, *args, **options):
        spec = TenantProvisionSpec(
            slug=options['slug'],
            name=options['name'],
            oib=options['oib'],
            street=options['street'],
            house_number=options['house_number'],
            postal_code=options['postal_code'],
            city=options['city'],
            email=options['email'],
            vat_status=options['vat_status'],
            vat_id=options['vat_id'],
            phone=options['phone'],
            website=options['website'],
            tax_office_code=options['tax_office_code'],
            owner_username=options['owner_username'],
            vat_year=options['vat_year'],
            vat_month=options['vat_month'],
        )
        update_existing = options['update_existing']

        if options['dry_run']:
            plan = validate_provision(spec, update_existing=update_existing)
            for warning in plan.warnings:
                self.stdout.write(self.style.WARNING(warning))
            for action in plan.actions:
                self.stdout.write(f'dry-run: {action}')
            if plan.errors:
                raise CommandError('; '.join(plan.errors))
            self.stdout.write(self.style.SUCCESS('dry-run: validacija prošla, nema upisa.'))
            return

        try:
            result = provision_tenant(spec, update_existing=update_existing)
        except TenantProvisionConflict as exc:
            raise CommandError('; '.join(_messages(exc))) from exc
        except TenantProvisionError as exc:
            raise CommandError('; '.join(_messages(exc))) from exc

        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(warning))
        if result.created:
            self.stdout.write(self.style.SUCCESS(
                f'Tenant {spec.slug} kreiran: {result.accounts} konta, '
                f'{result.posting_rules} pravila.'
            ))
        elif result.changed:
            self.stdout.write(self.style.SUCCESS(
                f'Tenant {spec.slug} ažuriran: {", ".join(result.changed)}.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Tenant {spec.slug} već postoji, bez izmjena.'
            ))


def _messages(exc) -> list[str]:
    if hasattr(exc, 'messages'):
        return list(exc.messages)
    return [str(exc)]
