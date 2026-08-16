from django.core.management.base import BaseCommand

from accounting.services.rrif_import import import_rrif_chart


class Command(BaseCommand):
    help = 'Uvezi RRIF-RP2025 kontni plan u platform predložak (RRIFChartEntry).'

    def add_arguments(self, parser):
        parser.add_argument('--csv', type=str, default='', help='Putanja do CSV datoteke')
        parser.add_argument('--clear', action='store_true', help='Obriši postojeće prije uvoza')

    def handle(self, *args, **options):
        csv_path = options['csv'] or None
        created, updated = import_rrif_chart(csv_path, clear=options['clear'])
        self.stdout.write(self.style.SUCCESS(f'RRIF import: {created} novih, {updated} ažuriranih.'))
