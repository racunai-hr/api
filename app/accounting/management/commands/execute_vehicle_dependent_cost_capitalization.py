"""Apply an approved vehicle dependent-cost capitalization snapshot."""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from domains.finance.services.vehicle_dependent_cost_capitalization import (
    execute_vehicle_dependent_cost_capitalization,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Execute vehicle dependent-cost capitalization from an approved plan snapshot. '
        'Atomic and fail-closed.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--asset-id', type=int, required=True)
        parser.add_argument('--expense-id', type=int, action='append', required=True)
        parser.add_argument('--case-id', type=str, required=True)
        parser.add_argument('--reason', type=str, required=True)
        parser.add_argument('--plan', type=str, required=True, help='JSON snapshot from plan --output')
        parser.add_argument('--user', type=str, default='')

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(slug=(options['tenant'] or '').strip())
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {options["tenant"]!r} does not exist.') from exc

        with open(options['plan'], encoding='utf-8') as handle:
            snapshot = json.load(handle)

        User = get_user_model()
        username = (options.get('user') or '').strip()
        if username:
            user = User.objects.filter(username=username).first()
            if user is None:
                raise CommandError(f'User {username!r} not found.')
        else:
            user = User.objects.filter(is_superuser=True).first()
            if user is None:
                raise CommandError('No superuser found; pass --user.')

        try:
            result = execute_vehicle_dependent_cost_capitalization(
                tenant,
                asset_id=options['asset_id'],
                expense_ids=options['expense_id'],
                snapshot=snapshot,
                user=user,
                reason=options['reason'],
                case_id=options['case_id'],
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        recon = (result.get('verify') or {}).get('reconciliation') or {}
        self.stdout.write(
            self.style.SUCCESS(
                f"asset={result['asset_id']} applied={len(result['applied'])} "
                f"capitalized_net={recon.get('capitalized_net')} "
                f"acquisition_cost={recon.get('acquisition_cost')} "
                f"balanced={recon.get('balanced')}"
            )
        )
