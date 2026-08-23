"""Read-only audit: document / GL / subledger mismatches (Korak 1)."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from domains.finance.services.document_integrity import audit_tenant_document_mismatches
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Read-only inventar nesklada između poslovnog dokumenta, GL temeljnica i '
        'SubledgerItem/Allocation. Ne mijenja bazu.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--tenant',
            type=str,
            required=True,
            help='Tenant slug (obavezno, npr. finestar)',
        )
        parser.add_argument(
            '--output',
            type=str,
            default='',
            help='Opcionalna putanja za JSON izlaz',
        )
        parser.add_argument(
            '--format',
            type=str,
            choices=['table', 'json'],
            default='table',
            help='Format stdout izlaza (default: table)',
        )
        parser.add_argument(
            '--include-p8',
            action='store_true',
            help='Uključi P8 (standard path OK) zapise u table stdout',
        )

    def handle(self, *args, **options):
        tenant_slug = (options['tenant'] or '').strip()
        if not tenant_slug:
            raise CommandError('--tenant je obavezan.')

        try:
            tenant = Tenant.objects.get(slug=tenant_slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {tenant_slug!r} ne postoji.') from exc

        report = audit_tenant_document_mismatches(tenant)
        output_path = (options.get('output') or '').strip()
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as handle:
                json.dump(report, handle, indent=2, ensure_ascii=False)
            self.stdout.write(self.style.SUCCESS(f'Wrote {output_path}'))

        if options['format'] == 'json':
            self.stdout.write(json.dumps(report, indent=2, ensure_ascii=False))
            return

        summary = report['summary']
        self.stdout.write(self.style.NOTICE(f"Tenant: {report['tenant']}"))
        self.stdout.write(f"Documents scanned: {summary['documents_scanned']}")
        self.stdout.write(f"Documents with mismatches: {summary['documents_with_mismatches']}")
        self.stdout.write(f"P8-only (standard path): {summary['documents_p8_only']}")
        self.stdout.write('Pattern counts:')
        for code, count in summary['pattern_counts'].items():
            self.stdout.write(f'  {code}: {count}')

        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Mismatch records:'))
        for row in report['records']:
            patterns = ', '.join(row['patterns'])
            self.stdout.write(
                f"  [{patterns}] {row['document_type']} {row['document_number']} "
                f"(id={row['document_id']}) partner={row['partner_name']}"
            )
            if row.get('orphan_subledger_items'):
                for orphan in row['orphan_subledger_items']:
                    self.stdout.write(
                        f"    orphan subledger id={orphan['id']} partner={orphan['partner_name']} "
                        f"amount={orphan['original_amount']}"
                    )

        if options['include_p8'] and report.get('p8_records'):
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('P8 standard path records:'))
            for row in report['p8_records']:
                self.stdout.write(
                    f"  {row['document_type']} {row['document_number']} (id={row['document_id']})"
                )
