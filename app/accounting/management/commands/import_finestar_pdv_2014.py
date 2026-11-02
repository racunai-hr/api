from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounting.services.tax_forms.pdv.import_paper import (
    PaperPdvImportError,
    import_finestar_paper_filings,
    import_finestar_pdv_2014_h1,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Arhivira nulte papirnate PDV prijave Fine Star (tromjesečja, mjeseci, godišnja).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (finestar)')
        parser.add_argument('--pdf', type=str, help='PDF tromjesečnih prijava I–VI 2014.')
        parser.add_argument('--dir', type=str, help='Direktorij s mjesečnim i godišnjom prijavom.')

    def handle(self, *args, **options):
        if bool(options['pdf']) == bool(options['dir']):
            raise CommandError('Navedite točno jedan od --pdf ili --dir.')
        try:
            tenant = Tenant.objects.get(slug=options['tenant'])
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant ne postoji: {options["tenant"]}') from exc
        try:
            if options['pdf']:
                results = import_finestar_pdv_2014_h1(tenant, Path(options['pdf']))
            else:
                results = import_finestar_paper_filings(tenant, Path(options['dir']))
        except PaperPdvImportError as exc:
            raise CommandError(str(exc)) from exc
        for period, outcome in results:
            self.stdout.write(f'{period} {outcome}')
        self.stdout.write(self.style.SUCCESS('Papirnate PDV prijave su arhivirane.'))
