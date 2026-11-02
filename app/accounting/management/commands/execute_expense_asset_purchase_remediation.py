"""Fail-closed write orchestrator for T-2026-0009 (requires explicit GO flags)."""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from domains.finance.services.expense_asset_purchase_remediation import (
    CASE_ID,
    execute_t20260009_asset_purchase_remediation,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Execute T-2026-0009 asset_purchase remediation. Default is dry-run (0 writes). '
        'Mutations require --execute --confirm-case T-2026-0009 --reason ... '
        '--i-understand-writes.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--expense-id', type=int, default=16)
        parser.add_argument(
            '--execute',
            action='store_true',
            help='Perform writes (still requires confirm flags).',
        )
        parser.add_argument(
            '--confirm-case',
            type=str,
            default='',
            help=f'Must equal {CASE_ID} when --execute is set.',
        )
        parser.add_argument(
            '--reason',
            type=str,
            default='',
            help='Audit reason (min 10 chars) when --execute is set.',
        )
        parser.add_argument(
            '--i-understand-writes',
            action='store_true',
            help='Required with --execute: acknowledge live accounting mutation.',
        )
        parser.add_argument('--format', choices=['table', 'json'], default='table')
        parser.add_argument('--user', type=str, default='', help='Username for audit/user FK')

    def handle(self, *args, **options):
        tenant_slug = (options['tenant'] or '').strip()
        try:
            tenant = Tenant.objects.get(slug=tenant_slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {tenant_slug!r} does not exist.') from exc

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

        execute = bool(options['execute'])
        if execute:
            if not options['i_understand_writes']:
                raise CommandError('--execute requires --i-understand-writes.')
            if options['confirm_case'] != CASE_ID:
                raise CommandError(f'--confirm-case must be exactly {CASE_ID!r}.')
            if len((options.get('reason') or '').strip()) < 10:
                raise CommandError('--reason must be at least 10 characters.')

        result = execute_t20260009_asset_purchase_remediation(
            tenant,
            user,
            execute=execute,
            reason=options.get('reason') or '',
            confirm_case_id=options.get('confirm_case') or '',
            expense_id=options['expense_id'],
        )
        payload = result.to_dict()

        if options['format'] == 'json':
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        else:
            mode = 'EXECUTED' if result.executed else ('DRY-RUN' if result.dry_run else 'BLOCKED')
            style = self.style.SUCCESS if result.prerequisites_ok and (
                result.executed or result.dry_run
            ) else self.style.ERROR
            self.stdout.write(style(f'[{mode}] {result.case_id} @ {result.tenant}'))
            self.stdout.write(
                f'prerequisites_ok={result.prerequisites_ok} '
                f'executed={result.executed} dry_run={result.dry_run}'
            )
            if result.blockers:
                self.stdout.write(self.style.ERROR('Blockers:'))
                for blocker in result.blockers:
                    self.stdout.write(f'  - {blocker}')
            if result.steps_completed:
                self.stdout.write('Steps completed:')
                for step in result.steps_completed:
                    self.stdout.write(f'  {step}')
            if result.result:
                self.stdout.write(f'Result: {result.result}')

        if not result.prerequisites_ok:
            raise CommandError('Fail-closed: prerequisites not met. No writes performed.')
        if execute and not result.executed:
            raise CommandError('Execute requested but remediation did not complete.')
