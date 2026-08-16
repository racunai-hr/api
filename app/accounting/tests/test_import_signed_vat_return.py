"""Tests for import_signed_vat_return — upload, archive import, validation."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import TestCase

from accounting.models import (
    SubmissionSource,
    VATPeriod,
    VATReturn,
    VATReturnSource,
    VATReturnStatus,
)
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.canonical import payload_hash
from accounting.services.tax_forms.pdv.diff import PayloadMismatchError
from accounting.services.tax_forms.pdv.import_return import import_signed_vat_return
from accounting.services.tax_forms.pdv.parse import parse_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.validation import PdvSchemaValidationError, validate_pdv_obrazac_xml
from accounting.services.tax_forms.pdv.vat_returns import create_vat_return_draft
from accounting.tests.test_pdv_checkpoint_april_2026 import PdvCheckpointApril2026Tests
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice

_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pdv'
_SUBMITTED_APRIL_2026 = _FIXTURES / 'submitted_april_2026.xml'
_ARCHIVE_FIXTURES = _FIXTURES / 'archive'


def _archive_xml_is_signed(path: Path) -> bool:
    try:
        validate_pdv_obrazac_xml(path.read_bytes(), signed=True)
    except PdvSchemaValidationError:
        return False
    return True


class ImportSignedVatReturnTests(PdvCheckpointApril2026Tests):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Fine Star d.o.o.',
            company_address='Bana Josipa Jelačića 58\n22000 Šibenik',
            street='Bana Josipa Jelačića',
            house_number='58',
            postal_code='22000',
            city='Šibenik',
            company_phone='+385 22 000 000',
            company_email='info@finestar.example',
            vat_number='36619131370',
            tax_office=tax_office,
        )
        cls.accountant = ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Zorana',
            last_name='Lambasa',
        )
        cls.signed_xml = _SUBMITTED_APRIL_2026.read_bytes()

    def test_signed_xml_matches_draft_via_field_comparison(self):
        draft = create_vat_return_draft(self.period, prepared_by=self.accountant)
        imported = parse_pdv_obrazac_xml(self.signed_xml)
        self.assertNotEqual(draft.payload_hash, payload_hash(imported))

        vat_return = import_signed_vat_return(
            self.period,
            self.signed_xml,
            vat_return=draft,
        )
        self.assertEqual(vat_return.status, VATReturnStatus.SUBMITTED)
        event = SubmissionService.current_submission(vat_return)
        if event is not None:
            self.assertEqual(event.source, SubmissionSource.IMPORT)
        self.assertTrue(default_storage.exists(vat_return.xml_submitted.name))
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'submitted')
        self.assertIsNotNone(self.period.submitted_at)
        validate_pdv_obrazac_xml(self.signed_xml, signed=True)

    def test_fast_hash_match_skips_field_comparison(self):
        draft = create_vat_return_draft(self.period, prepared_by=self.accountant)
        imported = parse_pdv_obrazac_xml(self.signed_xml)
        draft.payload_hash = payload_hash(imported)
        draft.save(update_fields=['payload_hash'])

        with patch(
            'accounting.services.tax_forms.pdv.import_return.compare_pdv_payload_fields',
        ) as mock_compare:
            import_signed_vat_return(self.period, self.signed_xml, vat_return=draft)
            mock_compare.assert_not_called()

    def test_mismatch_raises_with_sorted_differences(self):
        draft = create_vat_return_draft(self.period, prepared_by=self.accountant)
        tampered_str = self.signed_xml.decode('utf-8').replace('84.11', '85.11', 1)
        tampered_bytes = tampered_str.encode('utf-8')

        with self.assertRaises(PayloadMismatchError) as ctx:
            import_signed_vat_return(self.period, tampered_bytes, vat_return=draft)

        self.assertTrue(ctx.exception.differences)
        self.assertIn('razlika', ctx.exception.summary())
        draft.refresh_from_db()
        self.assertEqual(draft.status, VATReturnStatus.GENERATED)

    def test_archive_import_creates_imported_return(self):
        vat_return = import_signed_vat_return(
            self.period,
            self.signed_xml,
            vat_return=None,
            source=VATReturnSource.IMPORTED,
        )
        self.assertEqual(vat_return.status, VATReturnStatus.IMPORTED)
        self.assertEqual(vat_return.source, VATReturnSource.IMPORTED)
        event = SubmissionService.current_submission(vat_return)
        if event is not None:
            self.assertEqual(event.source, SubmissionSource.IMPORT)
        self.assertFalse(vat_return.xml_unsigned)
        self.assertTrue(default_storage.exists(vat_return.payload_json.name))
        self.assertTrue(default_storage.exists(vat_return.xml_submitted.name))
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'submitted')

    @patch('accounting.services.tax_forms.pdv.import_return.validate_pdv_obrazac_xml')
    def test_invalid_signed_xml_creates_no_record(self, mock_validate):
        mock_validate.side_effect = PdvSchemaValidationError('invalid')
        period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=2)

        with self.assertRaises(PdvSchemaValidationError):
            import_signed_vat_return(period, self.signed_xml, vat_return=None)

        self.assertEqual(VATReturn.all_objects.filter(vat_period=period).count(), 0)


class ImportPdvXmlCommandTests(TestCase):
    """Archive import via import_pdv_xml management command."""

    def test_command_imports_archive_fixtures(self):
        from settings.models import CompanySettings, TaxOffice
        from tenants.models import Tenant

        tenant = Tenant.objects.create(slug='finestar-archive-cmd', name='Fine Star archive')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        CompanySettings.all_objects.create(
            tenant=tenant,
            company_name='Fine Star d.o.o.',
            company_address='Bana Josipa Jelačića 58\n22000 Šibenik',
            street='Bana Josipa Jelačića',
            house_number='58',
            postal_code='22000',
            city='Šibenik',
            company_phone='+385 22 000 000',
            company_email='info@finestar.example',
            vat_number='36619131370',
            tax_office=tax_office,
        )

        xml_files = sorted(_ARCHIVE_FIXTURES.glob('PDV_*.xml'))
        signed_xml_files = [
            path for path in xml_files
            if _archive_xml_is_signed(path)
        ]
        self.assertGreaterEqual(
            len(signed_xml_files),
            4,
            'Arhiva mora sadržavati barem potpisane fixturee 01–04/2026',
        )

        expected_periods = {
            (parsed.period_from.year, parsed.period_from.month)
            for parsed in (parse_pdv_obrazac_xml(path.read_bytes()) for path in signed_xml_files)
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_dir = Path(tmpdir)
            for path in signed_xml_files:
                shutil.copy(path, archive_dir / path.name)
            call_command('import_pdv_xml', tenant=tenant.slug, dir=str(archive_dir))

        self.assertEqual(
            VATPeriod.all_objects.filter(tenant=tenant).count(),
            len(expected_periods),
        )

        for xml_path in signed_xml_files:
            parsed = parse_pdv_obrazac_xml(xml_path.read_bytes())
            period = VATPeriod.all_objects.get(
                tenant=tenant,
                year=parsed.period_from.year,
                month=parsed.period_from.month,
            )
            self.assertEqual(period.status, 'submitted')
            vat_return = period.returns.get()
            self.assertEqual(vat_return.status, VATReturnStatus.IMPORTED)
            self.assertEqual(vat_return.source, VATReturnSource.IMPORTED)
            event = SubmissionService.current_submission(vat_return)
            if event is not None:
                self.assertEqual(event.source, SubmissionSource.IMPORT)
