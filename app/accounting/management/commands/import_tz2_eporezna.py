"""Import an already-submitted Obrazac TZ 2 (XML + PDF evidence) into a tenant year."""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounting.services.tax_forms.tz2.import_tz2 import (
    CAROLINA_2026_PROCESSED_PDF,
    CAROLINA_2026_SUBMITTED_PDF,
    CAROLINA_2026_XML,
    Tz2ImportError,
    import_tz2_from_xml,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Uvezi predani Obrazac TZ 2 iz ePorezna XML-a i dvije PDF potvrde '
        '(predano + obrađeno). Idempotentno ako event s portalnim Identifikatorom već postoji.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--slug', type=str, required=True, help='Tenant slug')
        parser.add_argument('--year', type=int, default=2026)
        parser.add_argument('--xml', type=str, default=str(CAROLINA_2026_XML))
        parser.add_argument('--submitted-pdf', type=str, default=str(CAROLINA_2026_SUBMITTED_PDF))
        parser.add_argument('--processed-pdf', type=str, default=str(CAROLINA_2026_PROCESSED_PDF))
        parser.add_argument('--user', type=str, default='', help='Username for submitted_by')

    def handle(self, *args, **options):
        slug = (options['slug'] or '').strip()
        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant {slug!r} ne postoji.') from exc

        xml_path = Path(options['xml'])
        submitted_pdf_path = Path(options['submitted_pdf'])
        processed_pdf_path = Path(options['processed_pdf'])
        for path in (xml_path, submitted_pdf_path, processed_pdf_path):
            if not path.is_file():
                raise CommandError(f'Datoteka ne postoji: {path}')

        User = get_user_model()
        username = (options.get('user') or '').strip()
        if username:
            user = User.objects.filter(username=username).first()
            if user is None:
                raise CommandError(f'Korisnik {username!r} nije pronađen.')
        else:
            user = User.objects.filter(is_superuser=True).first()
            if user is None:
                raise CommandError('Nema superusera; navedite --user.')

        try:
            result = import_tz2_from_xml(
                tenant,
                options['year'],
                xml_path.read_bytes(),
                submitted_pdf_bytes=submitted_pdf_path.read_bytes(),
                processed_pdf_bytes=processed_pdf_path.read_bytes(),
                submitted_by=user,
            )
        except Tz2ImportError as exc:
            raise CommandError(str(exc)) from exc

        verb = 'uvezen' if result.created else 'već postoji'
        self.stdout.write(
            self.style.SUCCESS(
                f'TZ2 {options["year"]} v{result.tz2_return.version} {verb}: '
                f'#{result.submitted_event.submission_no} {result.submitted_event.external_identifier} '
                f'+ #{result.processed_event.submission_no} {result.processed_event.external_identifier}'
            )
        )
