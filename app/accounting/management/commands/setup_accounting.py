from django.core.management.base import BaseCommand

from accounting.services.rrif_import import import_rrif_chart
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules
from accounting.services.vat import get_or_create_vat_period
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Kompletna inicijalizacija računovodstva: RRIF import, kontni plan, pravila, PDV razdoblje.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, default='demo')
        parser.add_argument('--vat-year', type=int, default=2026)
        parser.add_argument('--vat-month', type=int, default=5)

    def handle(self, *args, **options):
        created, updated = import_rrif_chart()
        self.stdout.write(f'RRIF: {created} novih, {updated} ažuriranih.')

        tenant = Tenant.objects.get(slug=options['tenant'])
        accounts = provision_tenant_chart(tenant, update_names=True)
        rules = ensure_default_posting_rules(tenant)
        period = get_or_create_vat_period(tenant, options['vat_year'], options['vat_month'])

        self.stdout.write(self.style.SUCCESS(
            f'{tenant.slug}: {accounts} konta, {rules} pravila, PDV razdoblje {period}.'
        ))
