"""Production regression: compare ERP payload with submitted Obrazac PDV XML."""

from __future__ import annotations

import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounting.models import VATPeriod
from accounting.services.tax_forms.pdv.aggregate import aggregate_vat_boxes
from accounting.services.tax_forms.pdv.build import accountant_for_tenant, build_pdv_payload
from accounting.services.tax_forms.pdv.diff import compare_pdv_payload_fields
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.payload import PdvFieldPair, PdvFormHeader
from accounting.services.tax_forms.pdv.render import render_pdv_obrazac_xml
from accounting.services.tax_projection.rebuild import rebuild_vat_ledger
from settings.models import CompanySettings
from tenants.models import Tenant

_IMPLEMENTED_BOXES = ('201', '202', '203', '303', '400')


class Command(BaseCommand):
    help = (
        'Uspoređuje ERP PdvPayload s predanim Obrazac PDV XML-om '
        '(regresija nakon mjesečnog obračuna).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)
        parser.add_argument('--xml', type=str, help='Putanja do potpisanog PDV_*.xml')
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
            result = rebuild_vat_ledger(tenant, year, month, actor=None, replace=True)
            timings['rebuild_vat_ledger'] = time.perf_counter() - t0
            if not result.ok:
                raise CommandError(result.message, returncode=result.exit_code())
            self.stdout.write(result.message)
            period = VATPeriod.all_objects.get(pk=result.period_id)

        if options['benchmark']:
            t0 = time.perf_counter()
            aggregate_vat_boxes(period)
            timings['aggregate_vat_boxes'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        erp_payload = build_pdv_payload(period)
        timings['build_pdv_payload'] = time.perf_counter() - t0

        if options['benchmark']:
            header = self._form_header(period)
            t0 = time.perf_counter()
            render_pdv_obrazac_xml(erp_payload, header=header)
            timings['render_pdv_obrazac_xml'] = time.perf_counter() - t0
            timings['full_pipeline'] = sum(timings.values())

        xml_path = options.get('xml')
        if xml_path:
            path = Path(xml_path)
            if not path.is_file():
                raise CommandError(f'XML datoteka ne postoji: {path}')
            submitted = parse_pdv_obrazac_xml(path.read_bytes())
            differences = compare_pdv_payload_fields(erp_payload, submitted)

            self.stdout.write(
                f'\nPDV {month:02d}/{year} — tenant {tenant.slug}\n'
                f'Implementirani boxovi: {", ".join(_IMPLEMENTED_BOXES)}\n'
            )
            for code in _IMPLEMENTED_BOXES:
                erp_val = erp_payload.fields.get(code)
                sub_val = submitted.fields.get(code)
                if isinstance(erp_val, PdvFieldPair) and isinstance(sub_val, PdvFieldPair):
                    match = erp_val.vrijednost == sub_val.vrijednost and erp_val.porez == sub_val.porez
                    status = 'OK' if match else 'RAZLIKA'
                    self.stdout.write(
                        f'  {code}: ERP {erp_val.vrijednost}/{erp_val.porez} '
                        f'vs XML {sub_val.vrijednost}/{sub_val.porez} [{status}]'
                    )
                else:
                    match = erp_val == sub_val
                    status = 'OK' if match else 'RAZLIKA'
                    self.stdout.write(f'  {code}: ERP {erp_val} vs XML {sub_val} [{status}]')

            if differences:
                self.stderr.write(self.style.ERROR(f'\nUkupno {len(differences)} razlika u payloadu:'))
                for diff in differences:
                    self.stderr.write(f'  {diff.field} ({diff.kind}): {diff.expected} → {diff.actual}')
                raise CommandError('Regresija nije prošla — ERP != predani XML.')

            self.stdout.write(self.style.SUCCESS('\nRegresija OK — ERP podudara se s predanim XML-om.'))
        else:
            self.stdout.write(
                f'ERP payload za {month:02d}/{year}: '
                f'400={erp_payload.fields.get("400")} '
                f'(bez --xml usporedbe)'
            )

        if options['benchmark']:
            self.stdout.write('\nTrajanje (s):')
            for step, elapsed in timings.items():
                self.stdout.write(f'  {step}: {elapsed:.3f}')

    @staticmethod
    def _form_header(period: VATPeriod) -> PdvFormHeader:
        settings = CompanySettings.all_objects.filter(tenant=period.tenant).first()
        tax_office_code = settings.tax_office.code if settings and settings.tax_office_id else ''
        person = accountant_for_tenant(period.tenant)
        if person is not None:
            first_name, last_name = person.first_name, person.last_name
        else:
            first_name, last_name = 'NA', 'NA'
        return PdvFormHeader(
            tax_office_code=tax_office_code,
            prepared_by_first_name=first_name,
            prepared_by_last_name=last_name,
        )
