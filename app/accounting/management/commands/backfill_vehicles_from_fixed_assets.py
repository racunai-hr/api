"""Create Vehicle rows from existing FixedAsset VIN snapshots.

Default is dry-run. Writes require --execute. Linking goes through
link_vehicle_to_fixed_asset — never Vehicle.objects.create(fixed_asset=...).
"""

from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError

from accounting.models import VEHICLE_VIN_FORMAT_REGEX, FixedAsset, Vehicle
from domains.assets.services.vehicle import link_vehicle_to_fixed_asset
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Backfill Vehicle from FixedAsset VIN snapshots. '
        'Default is dry-run; mutations require --execute.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, default='', help='Tenant slug (optional)')
        parser.add_argument(
            '--execute',
            action='store_true',
            help='Create and link vehicles. Without this flag the command is dry-run.',
        )

    def handle(self, *args, **options):
        tenant_slug = (options.get('tenant') or '').strip()
        tenant = None
        if tenant_slug:
            try:
                tenant = Tenant.objects.get(slug=tenant_slug)
            except Tenant.DoesNotExist as exc:
                raise CommandError(f'Tenant {tenant_slug!r} does not exist.') from exc

        assets = FixedAsset.all_objects.exclude(vin='')
        if tenant is not None:
            assets = assets.filter(tenant=tenant)
        assets = assets.order_by('id')

        vin_re = re.compile(VEHICLE_VIN_FORMAT_REGEX)
        invalid: list[FixedAsset] = []
        candidates: list[FixedAsset] = []
        skipped: list[tuple[FixedAsset, str]] = []

        for asset in assets:
            vin = (asset.vin or '').strip().upper()
            if not vin_re.match(vin):
                invalid.append(asset)
                continue
            if Vehicle.all_objects.filter(fixed_asset=asset).exists():
                skipped.append((asset, 'already_linked'))
                continue
            if Vehicle.all_objects.filter(tenant_id=asset.tenant_id, vin=vin).exists():
                skipped.append((asset, 'vin_exists'))
                continue
            candidates.append(asset)

        self.stdout.write(f'invalid={len(invalid)} candidates={len(candidates)} skipped={len(skipped)}')
        if invalid:
            self.stdout.write(self.style.WARNING('Invalid VIN (not written):'))
            for asset in invalid:
                self.stdout.write(f'  FA #{asset.pk} vin={asset.vin!r} name={asset.name!r}')
        if skipped:
            self.stdout.write('Skipped:')
            for asset, reason in skipped:
                self.stdout.write(f'  FA #{asset.pk} vin={asset.vin} reason={reason}')

        execute = bool(options['execute'])
        if not execute:
            self.stdout.write(self.style.NOTICE('dry-run (pass --execute to write)'))
            for asset in candidates:
                self.stdout.write(
                    f'  would create Vehicle name={asset.name!r} vin={asset.vin} '
                    f'plate={asset.registration_plate!r} fa=#{asset.pk}'
                )
            return

        created = 0
        for asset in candidates:
            vehicle = Vehicle.all_objects.create(
                tenant=asset.tenant,
                name=asset.name,
                vin=asset.vin,
                registration_plate=asset.registration_plate,
            )
            link_vehicle_to_fixed_asset(vehicle, asset)
            created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'  created Vehicle #{vehicle.pk} vin={vehicle.vin} fa=#{asset.pk}'
                )
            )
        self.stdout.write(self.style.SUCCESS(f'created={created}'))
