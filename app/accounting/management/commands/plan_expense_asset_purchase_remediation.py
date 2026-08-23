"""Read-only dry-run remediation plan for T-2026-0009 (Korak 2c)."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from domains.finance.services.expense_asset_purchase_remediation import (
    plan_t20260009_asset_purchase_remediation,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Read-only dry-run remediation plan for expense T-2026-0009 '
        '(asset_purchase bypass). Never writes. Fail-closed on prerequisite drift.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (required)')
        parser.add_argument('--expense-id', type=int, default=16, help='Expense id (default 16)')
        parser.add_argument(
            '--format',
            type=str,
            choices=['table', 'json'],
            default='table',
            help='Stdout format',
        )
        parser.add_argument('--output', type=str, default='', help='Optional JSON output path')

    def handle(self, *args, **options):
        tenant_slug = (options['tenant'] or '').strip()
        if not tenant_slug:
            raise CommandError('--tenant is required.')

        try:
            tenant = Tenant.objects.get(slug=tenant_slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {tenant_slug!r} does not exist.') from exc

        plan = plan_t20260009_asset_purchase_remediation(
            tenant,
            expense_id=options['expense_id'],
        )
        payload = plan.to_dict()

        output_path = (options.get('output') or '').strip()
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            self.stdout.write(self.style.SUCCESS(f'Wrote {output_path}'))

        if options['format'] == 'json':
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            self._print_table(plan)

        if not plan.prerequisites_ok:
            raise CommandError(
                'Fail-closed: remediation prerequisites not met. No writes performed.'
            )

    def _print_table(self, plan) -> None:
        status = 'READY' if plan.prerequisites_ok else 'BLOCKED'
        style = self.style.SUCCESS if plan.prerequisites_ok else self.style.ERROR
        self.stdout.write(style(f'[{status}] {plan.case_id} @ {plan.tenant}'))
        self.stdout.write(f'dry_run={plan.dry_run} writes_allowed={plan.writes_allowed}')
        self.stdout.write(f'patterns: {", ".join(plan.patterns) or "(none)"}')
        self.stdout.write('')

        self.stdout.write(self.style.NOTICE('Prerequisites:'))
        for item in plan.prerequisites:
            mark = 'OK' if item.ok else 'FAIL'
            line = f'  [{mark}] {item.key}: expected={item.expected!r} actual={item.actual!r}'
            if item.detail:
                line += f' — {item.detail}'
            if item.ok:
                self.stdout.write(line)
            else:
                self.stdout.write(self.style.ERROR(line))

        if plan.blockers:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('Blockers:'))
            for blocker in plan.blockers:
                self.stdout.write(f'  - {blocker}')

        before = plan.before
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Before:'))
        expense = before.get('expense') or {}
        self.stdout.write(
            f"  Expense {expense.get('id')} {expense.get('expense_number')} "
            f"status={expense.get('status')} profile={expense.get('posting_profile')} "
            f"amount={expense.get('amount')} supplier={expense.get('supplier_id')}"
        )
        je49 = before.get('je49') or {}
        self.stdout.write(
            f"  JE49 {je49.get('id')} {je49.get('entry_number')} "
            f"status={je49.get('status')} lines={je49.get('lines')}"
        )
        item28 = before.get('item28') or {}
        self.stdout.write(
            f"  Item28 {item28.get('id')} status={item28.get('status')} "
            f"partner={item28.get('partner_id')} amount={item28.get('original_amount')}"
        )
        btx18 = before.get('btx18') or {}
        self.stdout.write(
            f"  BTX18 {btx18.get('id')} match={btx18.get('match_status')} "
            f"→ JE {btx18.get('matched_journal_entry_id')} amount={btx18.get('amount')}"
        )
        fa2 = before.get('fixed_asset2') or {}
        self.stdout.write(
            f"  FA2 {fa2.get('id')} {fa2.get('name')} status={fa2.get('status')} "
            f"purchase_je={fa2.get('purchase_journal_entry_id')} "
            f"cost={fa2.get('acquisition_cost')}"
        )
        je57 = before.get('je57') or {}
        self.stdout.write(
            f"  JE57 {je57.get('id')} status={je57.get('status')} "
            f"(out of scope) lines={je57.get('lines')}"
        )

        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Planned after (not applied):'))
        after = plan.planned_after
        for key in (
            'expense',
            'je49',
            'new_obligation_je',
            'new_payment_je',
            'item28',
            'btx18',
            'fixed_asset2',
            'je57',
        ):
            self.stdout.write(f'  {key}: {after.get(key)}')

        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Steps:'))
        for step in plan.steps:
            writes = 'WRITE' if step.get('writes') else 'read'
            self.stdout.write(
                f"  {step.get('seq')}. [{writes}] {step.get('action')} → {step.get('target')}: "
                f"{step.get('detail')}"
            )

        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Forbidden:'))
        for item in plan.forbidden:
            self.stdout.write(f'  - {item}')
        self.stdout.write(self.style.WARNING('Out of scope:'))
        for item in plan.out_of_scope:
            self.stdout.write(f'  - {item}')
