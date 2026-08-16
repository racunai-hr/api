from django.core.management.base import BaseCommand

from expenses.services.manual_supplier_seed import seed_manual_suppliers
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Kreira ručno definirane dobavljače (npr. strani prodavatelji vozila).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', default='finestar', help='Tenant slug (default: finestar)')
        parser.add_argument('--dry-run', action='store_true', help='Samo prikaži što bi se dogodilo')
        parser.add_argument('--update-existing', action='store_true', help='Ažuriraj postojeće zapise')

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'])
        result = seed_manual_suppliers(
            tenant=tenant,
            dry_run=options['dry_run'],
            update_existing=options['update_existing'],
        )
        for line in result.details:
            self.stdout.write(line)
        self.stdout.write(
            self.style.SUCCESS(
                f'Gotovo: +{result.created} ~{result.updated} ={result.skipped}',
            ),
        )
