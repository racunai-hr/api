"""Backfill submission payload_hash and confirmation attachments for May 2026."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from accounting.models import SubmissionEvent, TaxDocumentType, VATPeriod, VATReturn
from accounting.services.submission.protocol import pdv_s_payload_hash_from_xml
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.canonical import payload_hash as pdv_payload_hash
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from tenants.models import Tenant

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / 'tests' / 'fixtures' / 'pdv' / 'archive'
DEFAULT_PDV_XML = _FIXTURE_DIR / 'PDV_36619131370_20260501-20260531_v4_submitted.xml'
DEFAULT_PDV_S_XML = _FIXTURE_DIR / 'PDV-S_36619131370_20260501-20260531_submitted.xml'

BACKFILL_TARGETS = (
    {
        'document_type': TaxDocumentType.PDV,
        'portal_uuid': UUID('f5e5437e-0a31-4d8e-8c9a-8275541f082d'),
        'default_xml': DEFAULT_PDV_XML,
    },
    {
        'document_type': TaxDocumentType.PDV_S,
        'portal_uuid': UUID('3d50e215-cf30-48a9-93c0-b68da8182ecb'),
        'default_xml': DEFAULT_PDV_S_XML,
    },
)


class Command(BaseCommand):
    help = 'Backfill payload_hash i confirmation_attachment za SubmissionEvent 05/2026 (Fine Star).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, default='finestar')
        parser.add_argument('--year', type=int, default=2026)
        parser.add_argument('--month', type=int, default=5)
        parser.add_argument('--pdv-xml', type=str, default=str(DEFAULT_PDV_XML))
        parser.add_argument('--pdv-s-xml', type=str, default=str(DEFAULT_PDV_S_XML))
        parser.add_argument('--dry-run', action='store_true', default=False)

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'])
        period = VATPeriod.all_objects.get(
            tenant=tenant,
            year=options['year'],
            month=options['month'],
        )
        xml_paths = {
            TaxDocumentType.PDV: Path(options['pdv_xml']),
            TaxDocumentType.PDV_S: Path(options['pdv_s_xml']),
        }

        for target in BACKFILL_TARGETS:
            self._backfill_target(
                period,
                document_type=target['document_type'],
                portal_uuid=target['portal_uuid'],
                xml_path=xml_paths[target['document_type']],
                dry_run=options['dry_run'],
            )

    def _backfill_target(
        self,
        period,
        *,
        document_type: str,
        portal_uuid: UUID,
        xml_path: Path,
        dry_run: bool,
    ) -> None:
        if not xml_path.is_file():
            raise CommandError(f'XML datoteka ne postoji: {xml_path}')

        xml_bytes = xml_path.read_bytes()
        if document_type == TaxDocumentType.PDV:
            document = (
                VATReturn.all_objects.filter(vat_period=period)
                .order_by('-version')
                .first()
            )
            if document is None:
                raise CommandError(f'Nema VATReturn za {period}')
            computed_hash = pdv_payload_hash(parse_pdv_obrazac_xml(xml_bytes))
            fallback_hash = document.payload_hash
        else:
            document = period.pdv_s_return
            computed_hash = pdv_s_payload_hash_from_xml(xml_bytes)
            fallback_hash = document.get_payload_hash()

        event = SubmissionEvent.all_objects.filter(
            tenant=period.tenant,
            external_identifier=portal_uuid,
        ).first()
        if event is None:
            raise CommandError(
                f'Nema SubmissionEvent s portal UUID {portal_uuid} za {document_type}.',
            )

        payload_hash = computed_hash or fallback_hash
        self.stdout.write(
            f'{document_type}: event #{event.submission_no}, '
            f'hash={payload_hash[:16]}…, xml={xml_path.name}',
        )

        if dry_run:
            return

        if not event.payload_hash and payload_hash:
            SubmissionEvent.all_objects.filter(pk=event.pk).update(payload_hash=payload_hash)
            event.refresh_from_db()

        if not event.confirmation_attachment:
            SubmissionService.attach_confirmation(
                event,
                ContentFile(xml_bytes, name=xml_path.name),
                uploaded_by=None,
            )
            self.stdout.write(self.style.SUCCESS(f'  Prilog dodan za #{event.submission_no}'))
        else:
            self.stdout.write('  Prilog već postoji — preskočeno.')
