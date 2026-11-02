"""Read-only incident + remediation fingerprint gate for T-2026-0009."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from domains.finance.services.expense_asset_purchase_remediation import (
    verify_t20260009_incident_gate,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Read-only incident gate for T-2026-0009: proves JE178–181 / item61 '
        'accident is quiescent, fingerprint includes those locks, and remediation '
        'prerequisites stay READY. Never writes.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--expense-id', type=int, default=16)
        parser.add_argument('--format', choices=['table', 'json'], default='table')

    def handle(self, *args, **options):
        slug = (options['tenant'] or '').strip()
        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {slug!r} does not exist.') from exc

        plan = verify_t20260009_incident_gate(
            tenant,
            expense_id=options['expense_id'],
        )
        payload = plan.to_dict()

        if options['format'] == 'json':
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        else:
            status = 'READY' if plan.prerequisites_ok else 'BLOCKED'
            style = self.style.SUCCESS if plan.prerequisites_ok else self.style.ERROR
            self.stdout.write(style(f'[{status}] incident gate {plan.case_id} @ {plan.tenant}'))
            self.stdout.write(
                f'dry_run={plan.dry_run} writes_allowed={plan.writes_allowed} '
                f'checks={len(plan.prerequisites)}'
            )
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE('Incident locks:'))
            for item in plan.prerequisites:
                if not (
                    item.key.startswith('incident.')
                    or item.key == 'expense.posting_profile'
                ):
                    continue
                mark = 'OK' if item.ok else 'FAIL'
                line = (
                    f'  [{mark}] {item.key}: expected={item.expected!r} '
                    f'actual={item.actual!r}'
                )
                if item.detail:
                    line += f' — {item.detail}'
                if item.ok:
                    self.stdout.write(line)
                else:
                    self.stdout.write(self.style.ERROR(line))

            core_keys = {
                'btx18.matched_journal_entry_id',
                'fixed_asset2.purchase_journal_entry_id',
                'je57.lines',
                'item28.status',
                'je49.status',
                'patterns.required_set',
            }
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE('Core remediation fingerprint:'))
            for item in plan.prerequisites:
                if item.key not in core_keys:
                    continue
                mark = 'OK' if item.ok else 'FAIL'
                line = f'  [{mark}] {item.key}: actual={item.actual!r}'
                if item.ok:
                    self.stdout.write(line)
                else:
                    self.stdout.write(self.style.ERROR(line))

            if plan.blockers:
                self.stdout.write('')
                self.stdout.write(self.style.ERROR('Blockers:'))
                for blocker in plan.blockers:
                    self.stdout.write(f'  - {blocker}')

            incident = (plan.before or {}).get('incident_2026_08_23') or {}
            if incident:
                self.stdout.write('')
                self.stdout.write(self.style.NOTICE('Incident snapshot:'))
                for key, value in incident.items():
                    self.stdout.write(f'  {key}: {value!r}')

        if not plan.prerequisites_ok:
            raise CommandError(
                'Fail-closed: incident/remediation fingerprint not met. No writes performed.'
            )
