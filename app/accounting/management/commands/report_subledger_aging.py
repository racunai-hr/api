"""Partner aging izvještaj saldakonta (AR/AP)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounting.services.reports import (
    AGING_BUCKET_KEYS,
    AGING_BUCKET_LABELS,
    export_subledger_aging_xlsx,
    partner_aging,
    subledger_open_items,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Ispis partner aging izvještaja saldakonta (bucketi current, 1–30, …, 90+).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug')
        parser.add_argument(
            '--direction',
            choices=('receivable', 'payable', 'all'),
            default='all',
            help='Filtriraj potraživanja, obveze ili oboje (default: all)',
        )
        parser.add_argument(
            '--as-of',
            dest='as_of',
            type=str,
            help='Datum izvještaja (YYYY-MM-DD); default: danas',
        )
        parser.add_argument(
            '--xlsx',
            type=str,
            help='Putanja za XLSX export (npr. /tmp/aging.xlsx)',
        )

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(slug=options['tenant'])
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant "{options["tenant"]}" ne postoji.') from exc

        as_of = None
        if options['as_of']:
            try:
                as_of = datetime.strptime(options['as_of'], '%Y-%m-%d').date()
            except ValueError as exc:
                raise CommandError('--as-of mora biti YYYY-MM-DD.') from exc

        direction = None if options['direction'] == 'all' else options['direction']
        aging = partner_aging(tenant, direction=direction, as_of_date=as_of)
        open_items = subledger_open_items(tenant, direction=direction, as_of_date=as_of)
        as_of_date = aging['as_of_date']

        direction_label = {
            'receivable': 'Potraživanja (AR)',
            'payable': 'Obveze (AP)',
            None: 'AR + AP',
        }[direction]

        self.stdout.write(
            f'Saldakonti aging — {tenant.slug} — {direction_label} — na dan {as_of_date:%d.%m.%Y}'
        )
        self.stdout.write('')

        headers = ['Partner', 'Smjer'] + [AGING_BUCKET_LABELS[k] for k in AGING_BUCKET_KEYS] + ['Ukupno']
        col_widths = [max(len(h), 16) for h in headers]
        for partner_row in aging['partners']:
            col_widths[0] = max(col_widths[0], len(partner_row['partner_name']))
            col_widths[1] = max(col_widths[1], len(partner_row['direction_label']))

        self._print_row(headers, col_widths, bold=True)
        self.stdout.write(self._separator_line(col_widths))

        for partner_row in aging['partners']:
            cells = [partner_row['partner_name'], partner_row['direction_label']]
            for key in AGING_BUCKET_KEYS:
                cells.append(self._money(partner_row['buckets'][key]))
            cells.append(self._money(partner_row['total']))
            self._print_row(cells, col_widths)

        self.stdout.write(self._separator_line(col_widths))
        total_cells = ['UKUPNO']
        for key in AGING_BUCKET_KEYS:
            total_cells.append(self._money(aging['totals'][key]))
        total_cells.append(self._money(aging['totals']['total']))
        self._print_row(total_cells, col_widths, bold=True)

        self.stdout.write('')
        self.stdout.write(f'Otvorenih stavki: {len(open_items)}')

        if options['xlsx']:
            path = Path(options['xlsx'])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                export_subledger_aging_xlsx(tenant, direction=direction, as_of_date=as_of_date)
            )
            self.stdout.write(self.style.SUCCESS(f'XLSX spremljen: {path}'))

    @staticmethod
    def _money(value) -> str:
        return f'{value:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

    def _print_row(self, cells, widths, *, bold=False):
        line = '  '.join(str(cell).ljust(width) for cell, width in zip(cells, widths))
        if bold:
            self.stdout.write(self.style.MIGRATE_HEADING(line))
        else:
            self.stdout.write(line)

    @staticmethod
    def _separator_line(widths):
        return '  '.join('-' * width for width in widths)
