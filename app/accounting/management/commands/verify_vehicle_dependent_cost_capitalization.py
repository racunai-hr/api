"""Fail-closed verify for vehicle dependent-cost capitalization."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from domains.finance.services.vehicle_dependent_cost_capitalization import (
    verify_vehicle_dependent_cost_capitalization,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Verify capitalized_net == acquisition_cost and AP/VAT invariants.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--asset-id', type=int, required=True)
        parser.add_argument('--expense-id', type=int, action='append', required=True)
        parser.add_argument('--case-id', type=str, default='')
        parser.add_argument('--vat-before', type=str, default='', help='Optional JSON VAT 07 snapshot')

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(slug=(options['tenant'] or '').strip())
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {options["tenant"]!r} does not exist.') from exc

        vat_before = None
        vat_path = (options.get('vat_before') or '').strip()
        if vat_path:
            with open(vat_path, encoding='utf-8') as handle:
                payload = json.load(handle)
            vat_before = payload.get('vat_july', payload)

        result = verify_vehicle_dependent_cost_capitalization(
            tenant,
            asset_id=options['asset_id'],
            expense_ids=options['expense_id'],
            vat_before=vat_before,
            case_id=(options.get('case_id') or '').strip() or None,
        )
        if not result['ok']:
            for failure in result['failures']:
                self.stdout.write(self.style.ERROR(f'- {failure}'))
            raise CommandError('Verify failed.')
        recon = result['reconciliation']
        self.stdout.write(
            self.style.SUCCESS(
                f"OK capitalized_net={recon['capitalized_net']} "
                f"acquisition_cost={recon['acquisition_cost']}"
            )
        )
