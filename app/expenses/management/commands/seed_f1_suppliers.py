from django.core.management.base import BaseCommand, CommandError

from expenses.services.f1_supplier_extract import resolve_csv_paths
from expenses.services.supplier_seed import seed_suppliers_from_f1
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Kreira dobavljače iz F1 CSV datoteka pomoću statičke OIB mape.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument(
            '--path',
            type=str,
            required=True,
            help='Putanja do CSV datoteke ili direktorija s F1 CSV-ovima',
        )
        parser.add_argument('--dry-run', action='store_true', help='Pregled bez spremanja u bazu')
        parser.add_argument(
            '--update-existing',
            action='store_true',
            help='Ažuriraj postojeće dobavljače podacima iz mape',
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=options['tenant']).first()
        if not tenant:
            raise CommandError(f'Tenant "{options["tenant"]}" nije pronađen.')

        try:
            csv_paths = resolve_csv_paths(options['path'])
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc

        result = seed_suppliers_from_f1(
            tenant=tenant,
            paths=csv_paths,
            dry_run=options['dry_run'],
            update_existing=options['update_existing'],
        )

        total_oibs = result.created + result.skipped + result.updated + len(result.missing_in_map)
        mode = ' (dry-run)' if options['dry_run'] else ''
        self.stdout.write(
            f'{total_oibs} OIB-ova | {result.created} novih | '
            f'{result.skipped} preskočeno | {result.updated} ažurirano{mode}'
        )
        for line in result.details:
            self.stdout.write(f'  {line}')

        if result.missing_in_map:
            self.stdout.write(
                self.style.WARNING(
                    f'Upozorenje: {len(result.missing_in_map)} OIB-ova nije u mapi: '
                    f'{", ".join(result.missing_in_map)}'
                )
            )

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry-run — nema promjena u bazi.'))
        elif result.created or result.updated:
            self.stdout.write(self.style.SUCCESS('Seed dovršen.'))
