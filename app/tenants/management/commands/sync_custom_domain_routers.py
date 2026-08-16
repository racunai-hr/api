from django.core.management.base import BaseCommand

from tenants.traefik_sync import sync_traefik_custom_domains


class Command(BaseCommand):
    help = 'Generira Traefik dynamic routere za Tenant.custom_domain'

    def handle(self, *args, **options):
        path = sync_traefik_custom_domains()
        self.stdout.write(self.style.SUCCESS(f'Traefik config zapisana: {path}'))
