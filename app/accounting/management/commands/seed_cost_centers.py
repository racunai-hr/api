from django.core.management.base import BaseCommand

from domains.finance.services.cost_centers import PRESETS, ensure_default_cost_centers
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Idempotentno sjeme šifarnika mjesta troška za tenant.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument(
            '--preset',
            default='hospitality',
            choices=sorted(PRESETS.keys()),
            help='Preset šifarnika',
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=options['tenant']).first()
        if tenant is None:
            self.stderr.write(f'Tenant {options["tenant"]} nije pronađen.')
            return
        created = ensure_default_cost_centers(tenant, preset=options['preset'])
        self.stdout.write(
            self.style.SUCCESS(
                f'{tenant.slug}: {created} novih mjesta troška (preset={options["preset"]}).'
            )
        )
