from django.core.management.base import BaseCommand

from payroll.services.legal_data import ensure_payroll_tax_year, import_municipality_rates


class Command(BaseCommand):
    help = (
        'Uvezi/ažuriraj platformske zakonske parametre plaće (PayrollTaxYear) i '
        'stope poreza na dohodak po JLS (MunicipalityIncomeTaxRate) za navedenu godinu.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=2026, help='Godina (default 2026)')
        parser.add_argument('--csv', type=str, default='', help='Putanja do CSV-a stopa po JLS')

    def handle(self, *args, **options):
        year = options['year']
        tax_year = ensure_payroll_tax_year(year)
        self.stdout.write(self.style.SUCCESS(f'PayrollTaxYear {tax_year.year}: OK.'))

        created, updated = import_municipality_rates(year, options['csv'] or None)
        self.stdout.write(
            self.style.SUCCESS(
                f'Stope poreza po JLS za {year}.: {created} novih, {updated} ažuriranih.'
            )
        )
