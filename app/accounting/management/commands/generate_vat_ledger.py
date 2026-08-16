from django.core.management.base import BaseCommand

from accounting.services.vat import generate_vat_ledger
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Generira PDV knjige (I-RA izlazni, U-RA ulazni) za razdoblje.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)
        parser.add_argument('--replace', action='store_true')

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'])
        created, total = generate_vat_ledger(
            tenant,
            options['year'],
            options['month'],
            replace=options['replace'],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'PDV {options["month"]:02d}/{options["year"]}: {created} novih stavki, ukupno {total}.'
            )
        )
