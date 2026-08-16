from django.core.management.base import BaseCommand

from integrations.constants import IntegrationType
from integrations.manager import IntegrationManager
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Sync eRačun ulaznih računa i poll statusa izlaznih za tenant'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument('--inbound-only', action='store_true')
        parser.add_argument('--outbound-only', action='store_true')

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'], is_active=True)
        if IntegrationManager.get_connector(tenant, IntegrationType.ERACUN) is None:
            self.stderr.write(
                self.style.ERROR(
                    f'Nema aktivne eRačun integracije za tenant "{tenant.slug}".'
                )
            )
            return

        inbound_only = options['inbound_only']
        outbound_only = options['outbound_only']

        if not outbound_only:
            created = IntegrationManager.sync_eracun_inbound(tenant)
            self.stdout.write(self.style.SUCCESS(f'Ulazni sync: {created} novih troškova'))

        if not inbound_only:
            updated = IntegrationManager.poll_eracun_outbound(tenant)
            self.stdout.write(self.style.SUCCESS(f'Izlazni poll: {updated} ažuriranih statusa'))
