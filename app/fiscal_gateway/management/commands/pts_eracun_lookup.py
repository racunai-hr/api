from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from fiscal_gateway.client.eracun_lookup import EracunLookupError, prepare_eracun_send


class Command(BaseCommand):
    help = 'PTS Slanje eRačuna — parsira UBL XML, AMS + MPS lookup (korak 3–6 iz uputa)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--xml',
            type=str,
            default='fiscal_gateway/fixtures/pts_invoice.xml',
            help='Putanja do PTS generiranog UBL XML-a',
        )
        parser.add_argument(
            '--mps-base',
            type=str,
            default='',
            help='Opcionalni MPS base URL (npr. https://mps.racunai.hr/EracunMPS)',
        )

    def handle(self, *args, **options):
        xml_path = Path(options['xml'])
        if not xml_path.is_file():
            self.stderr.write(self.style.ERROR(f'XML nije pronađen: {xml_path}'))
            return

        mps_base = (options.get('mps_base') or '').strip() or None

        try:
            result = prepare_eracun_send(
                xml_path,
                verify_ssl=settings.FISCAL_CIS_VERIFY_SSL,
                mps_base=mps_base,
            )
        except EracunLookupError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        self.stdout.write(self.style.SUCCESS('PTS Slanje eRačuna — lookup OK'))
        self.stdout.write(f'Račun: {result.invoice_id}')
        self.stdout.write(
            f'Izdavatelj (SBD header): {result.supplier.name or "—"} / OIB {result.supplier.oib}'
        )
        self.stdout.write(
            f'Primatelj (AMS/MPS): {result.customer.name or "—"} / OIB {result.customer.oib}'
        )
        self.stdout.write('')
        self.stdout.write('AMS NAPTR:')
        self.stdout.write(f'  {result.ams_naptr}')
        self.stdout.write('')
        self.stdout.write('MPS URL:')
        self.stdout.write(f'  {result.mps_url}')
        self.stdout.write('')
        self.stdout.write(f'AS4 endpoint: {result.as4_endpoint}')
        self.stdout.write(f'PT primatelja (eb:To PartyId): {result.responder_ap_party_id}')
        self.stdout.write('')
        self.stdout.write('Slanje: python manage.py pts_eracun_send --xml <putanja>')
