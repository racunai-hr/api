"""Production regression: compare ERP ZpPayload with submitted Obrazac ZP XML."""

from __future__ import annotations

import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounting.models import VATPeriod
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes
from accounting.services.tax_forms.zp.build import build_zp_payload
from accounting.services.tax_forms.zp.parse import parse_zp_xml
from accounting.services.tax_forms.zp.render import render_zp_xml
from accounting.services.tax_forms.zp.verify import (
    compare_zp_payload_fields,
    verify_zp_against_pdv_boxes,
)
from accounting.services.vat import generate_vat_ledger
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Uspoređuje ERP ZpPayload s predanim Obrazac ZP XML-om '
        'i provjerava usklađenost zbrojeva s PDV boxovima 101/103.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)
        parser.add_argument('--xml', type=str, help='Putanja do potpisanog ZP_*.xml')
        parser.add_argument(
            '--regenerate-ledger',
            action='store_true',
            help='Ponovno generiraj PDV knjige prije usporedbe',
        )
        parser.add_argument(
            '--benchmark',
            action='store_true',
            help='Ispiši trajanje koraka pipelinea',
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'])
        year = options['year']
        month = options['month']

        period, _ = VATPeriod.all_objects.get_or_create(
            tenant=tenant,
            year=year,
            month=month,
        )

        timings: dict[str, float] = {}

        if options['regenerate_ledger']:
            t0 = time.perf_counter()
            created, total = generate_vat_ledger(
                tenant, year, month, replace=True,
            )
            timings['generate_vat_ledger'] = time.perf_counter() - t0
            self.stdout.write(f'Knjige: {created} novih stavki (ukupno {total}).')

        if options['benchmark']:
            t0 = time.perf_counter()
            aggregate_vat_boxes(period)
            timings['aggregate_vat_boxes'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        erp_payload = build_zp_payload(period)
        timings['build_zp_payload'] = time.perf_counter() - t0

        boxes = aggregate_vat_boxes(period)
        cross_check = verify_zp_against_pdv_boxes(
            erp_payload,
            pdv_box_101=boxes['101'].base,
            pdv_box_103=boxes['103'].base,
        )

        if options['benchmark']:
            t0 = time.perf_counter()
            render_zp_xml(erp_payload)
            timings['render_zp_xml'] = time.perf_counter() - t0
            timings['full_pipeline'] = sum(timings.values())

        self.stdout.write(
            f'\nZP {month:02d}/{year} — tenant {tenant.slug}\n'
            f'Redova: {len(erp_payload.rows)}\n'
            f'Zbroj dobara (I1): {erp_payload.total_goods}\n'
            f'Zbroj usluga (I4): {erp_payload.total_services}\n'
            f'PDV 101 (ledger): {cross_check.pdv_box_101}\n'
            f'PDV 103 (ledger): {cross_check.pdv_box_103}\n'
            f'Usklađenost ZP ↔ PDV 101/103: '
            f'{"OK" if cross_check.is_aligned else "RAZLIKA"}\n'
        )

        if not cross_check.is_aligned:
            self.stderr.write(
                self.style.ERROR(
                    'ZP zbrojevi ne odgovaraju PDV ledger boxovima 101/103.'
                )
            )
            raise CommandError('Cross-form provjera nije prošla — ZP != PDV 101/103.')

        xml_path = options.get('xml')
        if xml_path:
            path = Path(xml_path)
            if not path.is_file():
                raise CommandError(f'XML datoteka ne postoji: {path}')
            submitted = parse_zp_xml(path.read_bytes())
            differences = compare_zp_payload_fields(erp_payload, submitted)

            for row in erp_payload.rows:
                match_row = next(
                    (
                        r for r in submitted.rows
                        if r.country_code == row.country_code and r.pdv_id == row.pdv_id
                    ),
                    None,
                )
                if match_row is None:
                    status = 'NEDOSTAJE U XML'
                    self.stdout.write(
                        f'  {row.country_code}/{row.pdv_id}: '
                        f'ERP {row.goods_value}/{row.services_value} '
                        f'vs XML — [{status}]'
                    )
                    continue
                goods_match = row.goods_value == match_row.goods_value
                services_match = row.services_value == match_row.services_value
                status = 'OK' if goods_match and services_match else 'RAZLIKA'
                self.stdout.write(
                    f'  {row.country_code}/{row.pdv_id}: '
                    f'ERP {row.goods_value}/{row.services_value} '
                    f'vs XML {match_row.goods_value}/{match_row.services_value} [{status}]'
                )

            if differences:
                self.stderr.write(
                    self.style.ERROR(f'\nUkupno {len(differences)} razlika u payloadu:')
                )
                for diff in differences:
                    self.stderr.write(f'  {diff}')
                raise CommandError('Regresija nije prošla — ERP != predani XML.')

            self.stdout.write(
                self.style.SUCCESS('\nRegresija OK — ERP podudara se s predanim XML-om.')
            )
        else:
            self.stdout.write(
                f'(bez --xml usporedbe; dodaj --xml za provjeru predanog artefakta)'
            )

        if options['benchmark']:
            self.stdout.write('\nTrajanje (s):')
            for step, elapsed in timings.items():
                self.stdout.write(f'  {step}: {elapsed:.3f}')
