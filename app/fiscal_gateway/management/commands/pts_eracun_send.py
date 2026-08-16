from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from fiscal_gateway.client.as4_client import As4SendError, submit_invoice_via_domibus
from fiscal_gateway.client.eracun_lookup import EracunLookupError, prepare_eracun_send


class Command(BaseCommand):
    help = 'PTS Slanje eRačuna — SBDH + Domibus WS submit prema PU demo AS4 endpointu'

    def add_arguments(self, parser):
        parser.add_argument(
            '--xml',
            type=str,
            default='fiscal_gateway/fixtures/pts_invoice.xml',
            help='Putanja do PTS generiranog UBL XML-a',
        )
        parser.add_argument(
            '--lookup-only',
            action='store_true',
            help='Samo AMS/MPS lookup, bez slanja',
        )

    def handle(self, *args, **options):
        xml_path = Path(options['xml'])
        if not xml_path.is_file():
            self.stderr.write(self.style.ERROR(f'XML nije pronađen: {xml_path}'))
            return

        try:
            lookup = prepare_eracun_send(xml_path, verify_ssl=settings.FISCAL_CIS_VERIFY_SSL)
        except EracunLookupError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        self.stdout.write(self.style.SUCCESS('PTS Slanje eRačuna — lookup OK'))
        self.stdout.write(f'Račun: {lookup.invoice_id}')
        self.stdout.write(f'Izdavatelj (SBD): OIB {lookup.supplier.oib}')
        self.stdout.write(f'Primatelj (AMS/MPS): OIB {lookup.customer.oib}')
        self.stdout.write(f'AS4 endpoint: {lookup.as4_endpoint}')
        self.stdout.write(f'PT primatelja (eb:To PartyId): {lookup.responder_ap_party_id}')
        self.stdout.write('')

        if options['lookup_only']:
            return

        try:
            result = submit_invoice_via_domibus(xml_path)
        except (As4SendError, EracunLookupError) as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        self.stdout.write(self.style.SUCCESS('Domibus submit OK'))
        self.stdout.write(f'Message-ID: {result.message_id}')
        self.stdout.write(
            'Provjeri status poruke u Domibus adminu (Sent / Message log) i PTS portalu za test Slanje eRačuna.'
        )
