"""Apply an approved journal-link snapshot for one asset."""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.http import Http404

from domains.assets.services.journal_links import apply_asset_journal_links
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Create FixedAssetJournalLink rows from an explicit snapshot. '
        'Does not search VIN. Requires --plan or repeated --link.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--asset-id', type=int, required=True)
        parser.add_argument('--case-id', type=str, required=True)
        parser.add_argument('--reason', type=str, required=True)
        parser.add_argument('--plan', type=str, default='', help='JSON snapshot from plan command')
        parser.add_argument(
            '--link',
            action='append',
            default=[],
            help='JE_ID:role (repeatable). Alternative to --plan.',
        )
        parser.add_argument('--user', type=str, default='')

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(slug=(options['tenant'] or '').strip())
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {options["tenant"]!r} does not exist.') from exc

        entries = self._load_snapshot(options)
        if not entries:
            raise CommandError('Provide --plan <file.json> or at least one --link JE_ID:role.')

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
            result = apply_asset_journal_links(
                tenant,
                options['asset_id'],
                entries=entries,
                case_id=options['case_id'],
                reason=options['reason'],
                user=user,
            )
        except Http404 as exc:
            raise CommandError(f'Asset {options["asset_id"]} not found.') from exc
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"asset={result['asset_id']} created={result['created']} "
                f"existing={result['existing']}"
            )
        )

    def _load_snapshot(self, options) -> list[tuple[int, str]]:
        plan_path = (options.get('plan') or '').strip()
        links = options.get('link') or []
        if plan_path and links:
            raise CommandError('Use either --plan or --link, not both.')
        if plan_path:
            with open(plan_path, encoding='utf-8') as handle:
                payload = json.load(handle)
            if int(payload.get('asset_id') or 0) != int(options['asset_id']):
                raise CommandError(
                    f"Plan asset_id {payload.get('asset_id')!r} "
                    f"does not match --asset-id {options['asset_id']}."
                )
            rows = payload.get('entries') or []
            if not rows:
                raise CommandError('Plan file has empty entries snapshot.')
            return [(int(row['journal_entry_id']), str(row['role'])) for row in rows]
        parsed: list[tuple[int, str]] = []
        for raw in links:
            if ':' not in raw:
                raise CommandError(f'Invalid --link {raw!r}; expected JE_ID:role.')
            je_id, role = raw.split(':', 1)
            parsed.append((int(je_id), role.strip()))
        return parsed
