from django.core.management.base import BaseCommand, CommandError

from accounting.services.tax_projection.rebuild import RebuildOutcome, rebuild_vat_ledger
from accounting.services.tax_projection.switch import SwitchState, read_projection_write_switch
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Generira PDV knjige (I-RA/U-RA) za razdoblje. '
        'Kad je vat_projection_write uključen, koristi ADR-0019 projection apply.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)
        parser.add_argument('--replace', action='store_true')

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'])
        replace = options['replace']
        switch = read_projection_write_switch(tenant)
        if switch == SwitchState.ON and replace:
            self.stderr.write(
                self.style.WARNING(
                    'Engine put uvijek zamjenjuje origin=engine retke; '
                    '--replace je kompatibilni alias.'
                )
            )
        result = rebuild_vat_ledger(
            tenant,
            options['year'],
            options['month'],
            actor=None,
            replace=replace,
        )
        if result.ok:
            self.stdout.write(self.style.SUCCESS(result.message))
            return
        raise CommandError(result.message, returncode=result.exit_code())
