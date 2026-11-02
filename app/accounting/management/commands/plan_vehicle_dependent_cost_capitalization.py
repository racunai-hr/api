"""Read-only dry-run for vehicle dependent-cost capitalization."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from domains.finance.services.vehicle_dependent_cost_capitalization import (
    plan_vehicle_dependent_cost_capitalization,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Read-only plan for capitalizing vehicle dependent-cost expenses '
        '(4120 → 0373). Never writes.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--asset-id', type=int, required=True)
        parser.add_argument('--expense-id', type=int, action='append', required=True)
        parser.add_argument('--case-id', type=str, default='VEHICLE-DEP-COST')
        parser.add_argument('--format', type=str, choices=['table', 'json'], default='table')
        parser.add_argument('--output', type=str, default='')

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(slug=(options['tenant'] or '').strip())
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {options["tenant"]!r} does not exist.') from exc

        plan = plan_vehicle_dependent_cost_capitalization(
            tenant,
            asset_id=options['asset_id'],
            expense_ids=options['expense_id'],
            case_id=options['case_id'],
        )
        payload = plan.to_dict()

        output_path = (options.get('output') or '').strip()
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as handle:
                json.dump(payload['snapshot'], handle, indent=2, ensure_ascii=False)
            self.stdout.write(self.style.SUCCESS(f'Wrote snapshot {output_path}'))

        if options['format'] == 'json':
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            self._print_table(plan)

        if not plan.prerequisites_ok:
            raise CommandError('Fail-closed: remediation prerequisites not met. No writes performed.')

    def _print_table(self, plan) -> None:
        status = 'READY' if plan.prerequisites_ok else 'BLOCKED'
        style = self.style.SUCCESS if plan.prerequisites_ok else self.style.ERROR
        self.stdout.write(style(f'[{status}] {plan.case_id} asset={plan.asset_id} @ {plan.tenant}'))
        self.stdout.write(f'dry_run={plan.dry_run} writes_allowed={plan.writes_allowed}')
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Prerequisites:'))
        for item in plan.prerequisites:
            mark = 'OK' if item.ok else 'FAIL'
            line = f'  [{mark}] {item.key}: expected={item.expected!r} actual={item.actual!r}'
            if item.detail:
                line += f' — {item.detail}'
            self.stdout.write(line if item.ok else self.style.ERROR(line))
        if plan.blockers:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('Blockers:'))
            for blocker in plan.blockers:
                self.stdout.write(f'  - {blocker}')
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Expenses:'))
        for row in plan.expenses:
            self.stdout.write(
                f"  {row.get('expense_number')} id={row.get('expense_id')} "
                f"je={row.get('original_je_id')} net={row.get('net_amount')} "
                f"applied={row.get('already_applied')}"
            )
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Steps:'))
        for step in plan.steps:
            writes = 'WRITE' if step.get('writes') else 'read'
            self.stdout.write(
                f"  {step.get('seq')}. [{writes}] {step.get('action')} → {step.get('target')}: "
                f"{step.get('detail')}"
            )
