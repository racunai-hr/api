"""Fail-closed capitalization reconciliation gate for one asset."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.http import Http404

from domains.assets.services.journal_links import verify_asset_journal_links
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Verify capitalized_net == acquisition_cost for one asset. '
        'Non-zero exit when unbalanced.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--asset-id', type=int, required=True)
        parser.add_argument('--format', choices=['table', 'json'], default='table')

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(slug=(options['tenant'] or '').strip())
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {options["tenant"]!r} does not exist.') from exc

        try:
            result = verify_asset_journal_links(tenant, options['asset_id'])
        except Http404 as exc:
            raise CommandError(f'Asset {options["asset_id"]} not found.') from exc

        if options['format'] == 'json':
            self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            recon = result['reconciliation']
            self.stdout.write(
                f"asset={result['asset_id']} capitalized={recon['capitalized_net']} "
                f"acquisition={recon['acquisition_cost']} difference={recon['difference']}"
            )

        if not result['ok']:
            raise CommandError('Capitalization reconciliation is not balanced.')
        self.stdout.write(self.style.SUCCESS('balanced'))
