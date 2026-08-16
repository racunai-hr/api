import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from domains.assets.services.depreciation import run_monthly_depreciation_for_tenant
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Knjiži mjesečnu amortizaciju osnovnih sredstava za tenant i razdoblje.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument('--year', type=int, required=True, help='Godina razdoblja')
        parser.add_argument('--month', type=int, required=True, help='Mjesec razdoblja (1–12)')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Samo izračun bez knjiženja',
        )
        parser.add_argument(
            '--user',
            default='',
            help='Username za knjiženje (default: prvi superuser)',
        )

    def handle(self, *args, **options):
        tenant_slug = options['tenant']
        year = options['year']
        month = options['month']
        dry_run = options['dry_run']

        if month < 1 or month > 12:
            raise CommandError('Mjesec mora biti između 1 i 12.')

        try:
            tenant = Tenant.objects.get(slug=tenant_slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant "{tenant_slug}" ne postoji.') from exc

        User = get_user_model()
        if options['user']:
            try:
                user = User.objects.get(username=options['user'])
            except User.DoesNotExist as exc:
                raise CommandError(f'Korisnik "{options["user"]}" ne postoji.') from exc
        else:
            user = User.objects.filter(is_superuser=True).order_by('pk').first()
            if user is None:
                raise CommandError('Nema superuser korisnika za knjiženje.')

        result = run_monthly_depreciation_for_tenant(
            tenant,
            user,
            year=year,
            month=month,
            dry_run=dry_run,
        )

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(
            self.style.SUCCESS(
                f'{prefix}Amortizacija {month:02d}/{year} za {tenant_slug}: '
                f'{len(result["processed"])} obrađeno, {len(result["skipped"])} preskočeno',
            ),
        )
        self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False))
