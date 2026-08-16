"""Archive and verify a signed Obrazac PDV-S XML after ePorezna submission."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from lxml import etree

from accounting.models import SubmissionDestination, SubmissionSource, VATPeriod
from accounting.services.submission.events import get_or_create_pdv_s_return
from accounting.services.submission.protocol import pdv_s_payload_hash_from_xml
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows
from accounting.services.tax_forms.pdv_s.parse import parse_pdv_s_xml
from accounting.services.tax_forms.pdv_s.validation import PdvSSchemaValidationError, validate_pdv_s_xml
from tenants.models import Tenant

DEFAULT_ARCHIVE_DIR = Path('/opt/stacks/racunai.hr/.temp/PDV-S')
_METADATA_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'


def _extract_external_identifier(xml_bytes: bytes) -> UUID | None:
    root = etree.fromstring(xml_bytes)
    identifier = root.find(f'.//{{{_METADATA_NS}}}Identifikator')
    if identifier is None or not identifier.text:
        return None
    try:
        return UUID(identifier.text.strip())
    except ValueError:
        return None


class Command(BaseCommand):
    help = 'Provjeri potpisani PDV-S XML i arhiviraj nakon predaje na ePoreznu.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)
        parser.add_argument('--xml', type=str, required=True, help='Putanja do potpisanog PDV-S XML-a s ePorezne')
        parser.add_argument(
            '--archive-dir',
            type=str,
            default=str(DEFAULT_ARCHIVE_DIR),
            help='Ciljna mapa arhive (zadano: .temp/PDV-S/)',
        )
        parser.add_argument(
            '--external-identifier',
            type=str,
            default=None,
            help='Portal UUID predaje (obavezno uz --create-event; ne koristi XML Identifikator)',
        )
        parser.add_argument(
            '--create-event',
            action='store_true',
            default=False,
            help='Kreiraj SubmissionEvent nakon uspješne validacije (destination=eporezna)',
        )

    def handle(self, *args, **options):
        source = Path(options['xml'])
        if not source.is_file():
            raise CommandError(f'Datoteka ne postoji: {source}')

        xml_bytes = source.read_bytes()
        try:
            validate_pdv_s_xml(xml_bytes, signed=True)
        except PdvSSchemaValidationError as exc:
            raise CommandError(f'PDV-S validacija nije prošla: {exc}') from exc

        parsed = parse_pdv_s_xml(xml_bytes)
        tenant = Tenant.objects.get(slug=options['tenant'])
        period = VATPeriod.all_objects.get(
            tenant=tenant,
            year=options['year'],
            month=options['month'],
        )
        expected = aggregate_pdv_s_rows(period)

        if parsed.oib != expected.taxpayer.oib:
            raise CommandError(f'OIB mismatch: {parsed.oib} != {expected.taxpayer.oib}')
        if parsed.period_from != expected.period_from.isoformat():
            raise CommandError(
                f'Razdoblje mismatch: {parsed.period_from} != {expected.period_from.isoformat()}'
            )
        if parsed.period_to != expected.period_to.isoformat():
            raise CommandError(
                f'Razdoblje mismatch: {parsed.period_to} != {expected.period_to.isoformat()}'
            )
        if parsed.total_goods != expected.total_goods:
            raise CommandError(
                f'IsporukeUkupno I1 mismatch: {parsed.total_goods} != {expected.total_goods}'
            )
        if parsed.total_services != expected.total_services:
            raise CommandError(
                f'IsporukeUkupno I2 mismatch: {parsed.total_services} != {expected.total_services}'
            )
        if len(parsed.rows) != len(expected.rows):
            raise CommandError(
                f'Broj redova mismatch: {len(parsed.rows)} != {len(expected.rows)}'
            )

        for actual, exp in zip(parsed.rows, expected.rows, strict=True):
            if actual.country_code != exp.country_code or actual.pdv_id != exp.pdv_id:
                raise CommandError(
                    f'Red mismatch: {actual.country_code}{actual.pdv_id} != {exp.country_code}{exp.pdv_id}'
                )
            if actual.goods_amount != exp.goods_amount:
                raise CommandError(
                    f'I1 mismatch za {actual.country_code}{actual.pdv_id}: '
                    f'{actual.goods_amount} != {exp.goods_amount}'
                )
            if actual.services_amount != exp.services_amount:
                raise CommandError(
                    f'I2 mismatch za {actual.country_code}{actual.pdv_id}: '
                    f'{actual.services_amount} != {exp.services_amount}'
                )

        archive_dir = Path(options['archive_dir'])
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_name = (
            f'PDV-S_{expected.taxpayer.oib}_'
            f'{expected.period_from:%Y%m%d}-{expected.period_to:%Y%m%d}.xml'
        )
        archive_path = archive_dir / archive_name
        shutil.copy2(source, archive_path)

        event_msg = ''
        if options['create_event']:
            external_raw = options.get('external_identifier')
            if not external_raw:
                raise CommandError(
                    '--external-identifier (portal UUID) je obavezan uz --create-event.',
                )
            pdv_s_return = get_or_create_pdv_s_return(period)
            event = SubmissionService.create_event(
                pdv_s_return,
                destination=SubmissionDestination.EPOREZNA,
                external_identifier=UUID(external_raw),
                submitted_at=timezone.now(),
                submitted_by=None,
                source=SubmissionSource.IMPORT,
                version_confirmed=True,
                payload_hash=pdv_s_payload_hash_from_xml(xml_bytes),
            )
            event_msg = f', event #{event.submission_no} ({event.external_identifier})'

        self.stdout.write(
            self.style.SUCCESS(
                f'PDV-S {options["month"]:02d}/{options["year"]} arhiviran: '
                f'{len(parsed.rows)} red(ova), I1={parsed.total_goods}, potpis=da → {archive_path}'
                f'{event_msg}'
            )
        )
