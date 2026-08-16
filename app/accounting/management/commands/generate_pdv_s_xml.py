from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounting.models import VATPeriod
from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows
from accounting.services.tax_forms.pdv_s.render import render_pdv_s_xml
from accounting.services.tax_forms.pdv_s.validation import PdvSSchemaValidationError, validate_pdv_s_xml
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Generira Obrazac PDV-S XML za razdoblje (EU stjecanje).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)
        parser.add_argument(
            '--output',
            type=str,
            help='Putanja izlazne datoteke. Zadano: PDV-S_<OIB>_<YYYYMMDD>-<YYYYMMDD>.xml u cwd.',
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'])
        period = VATPeriod.all_objects.get(
            tenant=tenant,
            year=options['year'],
            month=options['month'],
        )
        payload = aggregate_pdv_s_rows(period)
        xml_bytes = render_pdv_s_xml(payload)
        try:
            validate_pdv_s_xml(xml_bytes)
        except PdvSSchemaValidationError as exc:
            raise CommandError(f'PDV-S XSD validacija nije prošla: {exc}') from exc

        if options['output']:
            output_path = Path(options['output'])
        else:
            settings_oib = payload.taxpayer.oib
            output_path = Path(
                f'PDV-S_{settings_oib}_{payload.period_from:%Y%m%d}-{payload.period_to:%Y%m%d}.xml'
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(xml_bytes)

        self.stdout.write(
            self.style.SUCCESS(
                f'PDV-S {options["month"]:02d}/{options["year"]}: '
                f'{len(payload.rows)} red(ova), I1={payload.total_goods}, I2={payload.total_services} → {output_path}'
            )
        )
