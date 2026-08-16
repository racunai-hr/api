from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounting.models import VATPeriod, VATReturnSource
from accounting.services.tax_forms.pdv.import_return import import_signed_vat_return
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Importira potpisane Obrazac PDV XML datoteke iz direktorija (arhiva).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument('--dir', type=str, required=True, help='Direktorij s PDV_*.xml datotekama')

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'])
        directory = Path(options['dir'])
        if not directory.is_dir():
            raise CommandError(f'Direktorij ne postoji: {directory}')

        xml_files = sorted(directory.glob('PDV_*.xml'))
        if not xml_files:
            raise CommandError(f'Nema PDV_*.xml datoteka u {directory}')

        imported = 0
        for xml_path in xml_files:
            xml_bytes = xml_path.read_bytes()
            parsed = parse_pdv_obrazac_xml(xml_bytes)
            period, _ = VATPeriod.all_objects.get_or_create(
                tenant=tenant,
                year=parsed.period_from.year,
                month=parsed.period_from.month,
            )
            vat_return = import_signed_vat_return(
                period,
                xml_bytes,
                vat_return=None,
                source=VATReturnSource.IMPORTED,
            )
            imported += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'{xml_path.name} → PDV {period.month:02d}/{period.year} '
                    f'v{vat_return.version} ({vat_return.get_status_display()})'
                )
            )

        self.stdout.write(self.style.SUCCESS(f'Uvezeno {imported} datoteka.'))
