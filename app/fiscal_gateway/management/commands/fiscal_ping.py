from django.core.management.base import BaseCommand
from django.utils import timezone

from fiscal_gateway.client.certificate import CertificateError, load_p12
from fiscal_gateway.client.cis_client import CISClient, CISClientError
from fiscal_gateway.config_utils import (
    FiscalConfigError,
    get_active_fiscal_config,
    resolve_p12_password,
    resolve_p12_path,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Test učitavanja demo .p12 certifikata i CIS reachability'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument('--skip-cis', action='store_true', help='Samo učitaj certifikat')

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'], is_active=True)
        try:
            config = get_active_fiscal_config(tenant)
        except FiscalConfigError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        p12_path = resolve_p12_path(config)
        p12_password = resolve_p12_password(config)

        try:
            material = load_p12(p12_path, p12_password)
        except CertificateError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        self.stdout.write(self.style.SUCCESS(f'Certifikat učitan: {material.subject}'))
        self.stdout.write(f'  OIB: {material.oib or config.oib}')
        self.stdout.write(f'  Vrijedi do: {material.not_valid_after.isoformat()}')
        self.stdout.write(f'  Putanja: {p12_path}')

        if options['skip_cis']:
            return

        client = CISClient(config)
        self.stdout.write(f'CIS endpoint ({config.cis_env}): {client.endpoint}')

        try:
            response = client.ping()
        except CISClientError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        config.last_ping_at = timezone.now()
        config.save(update_fields=['last_ping_at'])

        self.stdout.write(
            self.style.SUCCESS(
                f'CIS odgovor: HTTP {response.http_status} '
                f'(verify_ssl={client.verify_ssl})'
            )
        )
