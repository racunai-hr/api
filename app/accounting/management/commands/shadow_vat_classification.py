"""Read-only shadow VAT classification. Never writes VATLedgerEntry."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from accounting.models import VATPeriod
from accounting.services.tax_shadow.runner import shadow_classify_period
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Shadow-classify a VAT period against existing VATLedgerEntry (read-only).'

    def add_arguments(self, parser):
        parser.add_argument('--period-id', type=int, help='Canonical VATPeriod id')
        parser.add_argument('--tenant', type=str, help='Tenant slug (helper selector)')
        parser.add_argument('--year', type=int)
        parser.add_argument('--month', type=int)
        parser.add_argument('--json', action='store_true', help='Deterministic JSON on stdout')

    def handle(self, *args, **options):
        period = self._resolve_period(options)
        report = shadow_classify_period(period)
        payload = report.to_json()
        self.stdout.write(payload if options['json'] else payload)
        if report.exit_code() != 0:
            raise SystemExit(report.exit_code())

    def _resolve_period(self, options) -> VATPeriod:
        period_id = options.get('period_id')
        if period_id:
            try:
                return VATPeriod.all_objects.select_related('tenant').get(pk=period_id)
            except VATPeriod.DoesNotExist as exc:
                raise CommandError(f'VATPeriod {period_id} not found') from exc

        tenant_slug = options.get('tenant')
        year = options.get('year')
        month = options.get('month')
        if not (tenant_slug and year and month):
            raise CommandError('Provide --period-id or --tenant --year --month')
        tenant = Tenant.objects.get(slug=tenant_slug)
        matches = list(
            VATPeriod.all_objects.filter(tenant=tenant, year=year, month=month)
        )
        if len(matches) != 1:
            raise SystemExit(2)
        return matches[0]
