from django.core.management.base import BaseCommand

from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Kopira RRIF kontni plan u ChartOfAccounts za tenant(e).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, help='Tenant slug (npr. demo)')
        parser.add_argument('--all', action='store_true', help='Svi aktivni tenanti')
        parser.add_argument('--update-names', action='store_true', help='Ažuriraj nazive postojećih RRIF konta')
        parser.add_argument('--posting-rules', action='store_true', help='Kreiraj zadana pravila knjiženja')

    def handle(self, *args, **options):
        if options['all']:
            tenants = Tenant.objects.filter(is_active=True)
        elif options['tenant']:
            tenants = Tenant.objects.filter(slug=options['tenant'])
        else:
            self.stderr.write('Navedi --tenant=slug ili --all')
            return

        for tenant in tenants:
            created = provision_tenant_chart(tenant, update_names=options['update_names'])
            rules = 0
            if options['posting_rules']:
                rules = ensure_default_posting_rules(tenant)
            self.stdout.write(
                self.style.SUCCESS(
                    f'{tenant.slug}: {created} novih konta, {rules} novih pravila knjiženja.'
                )
            )
