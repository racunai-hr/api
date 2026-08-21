"""Read-only prepare dry-run with deterministic JSON evidence (Gate E)."""

from __future__ import annotations

import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from accounting.models import VATPeriod, VATProjectionRun
from accounting.services.tax_projection.prepare import prepare_vat_projection
from accounting.services.tax_shadow.runner import ledger_fingerprint
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Read-only prepare_vat_projection dry-run. Emits one JSON object to stdout. '
        'Fails if ledger fingerprint or VATProjectionRun count changes.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--year', type=int)
        parser.add_argument('--month', type=int)
        parser.add_argument('--period-id', type=int)

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'])
        if options.get('period_id'):
            period = VATPeriod.all_objects.get(pk=options['period_id'], tenant=tenant)
        elif options.get('year') and options.get('month'):
            period = VATPeriod.all_objects.get(
                tenant=tenant, year=options['year'], month=options['month'],
            )
        else:
            raise CommandError('Provide --period-id or --year and --month', returncode=2)

        fp_before = ledger_fingerprint(period)
        runs_before = VATProjectionRun.all_objects.filter(vat_period=period).count()
        candidate = prepare_vat_projection(period)
        fp_after = ledger_fingerprint(period)
        runs_after = VATProjectionRun.all_objects.filter(vat_period=period).count()

        payload = {
            'tenant_slug': tenant.slug,
            'period_id': period.pk,
            'year': period.year,
            'month': period.month,
            'period_status': period.status,
            'candidate_status': candidate.status.value,
            'writable': candidate.writable,
            'primary_rejection_code': candidate.primary_rejection_code,
            'issue_codes': dict(Counter(issue.code for issue in candidate.issues)),
            'engine_version': candidate.engine_version,
            'mapping_version': candidate.mapping_version,
            'input_fingerprint': candidate.input_fingerprint,
            'output_fingerprint': candidate.output_fingerprint,
            'candidate_row_count': len(candidate.rows),
            'ledger_fingerprint_before': fp_before,
            'ledger_fingerprint_after': fp_after,
            'vat_projection_run_count_before': runs_before,
            'vat_projection_run_count_after': runs_after,
            'read_only_ok': fp_before == fp_after and runs_before == runs_after,
        }
        self.stdout.write(json.dumps(payload, sort_keys=True, separators=(',', ':')))
        if not payload['read_only_ok']:
            raise CommandError(
                'Dry-run mutated ledger fingerprint or VATProjectionRun count',
                returncode=1,
            )
