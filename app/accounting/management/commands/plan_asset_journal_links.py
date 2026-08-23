"""Read-only VIN candidate plan for one asset. Never writes."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.http import Http404

from domains.assets.services.journal_links import plan_asset_journal_links
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Read-only plan of journal-link candidates for one asset. '
        'VIN is a proposer only — execute must use the snapshot. Never writes.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--asset-id', type=int, required=True)
        parser.add_argument('--format', choices=['table', 'json'], default='table')
        parser.add_argument('--output', type=str, default='')

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(slug=(options['tenant'] or '').strip())
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {options["tenant"]!r} does not exist.') from exc

        try:
            plan = plan_asset_journal_links(tenant, options['asset_id'])
        except Http404 as exc:
            raise CommandError(f'Asset {options["asset_id"]} not found.') from exc

        payload = plan.to_dict()
        output_path = (options.get('output') or '').strip()
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            self.stdout.write(self.style.SUCCESS(f'Wrote {output_path}'))

        if options['format'] == 'json':
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        self.stdout.write(f'asset={plan.asset_id} vin={plan.vin or "(none)"}')
        self.stdout.write('VIN is a candidate proposer only. Execute requires this snapshot.')
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Candidates:'))
        if not plan.candidates:
            self.stdout.write('  (none)')
        for row in plan.candidates:
            linked = 'linked' if row.already_linked else 'new'
            self.stdout.write(
                f'  [{linked}] {row.entry_number} #{row.journal_entry_id} '
                f'→ {row.proposed_role} ref={row.reference}'
            )
        if plan.excluded:
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE('Excluded:'))
            for item in plan.excluded:
                self.stdout.write(
                    f"  {item['entry_number']} #{item['journal_entry_id']} — {item['reason']}"
                )
