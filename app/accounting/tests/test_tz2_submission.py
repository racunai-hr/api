"""TZ2Return draft + submission tests. Predaja ne smije lockati VATPeriod."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounting.models import TaxDocumentType, TZ2Return, VATPeriod
from accounting.services.submission.events import get_submission_events
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.tz2.aggregate import alma_2026_input
from accounting.services.tax_forms.tz2.import_tz2 import (
    CAROLINA_2026_PROCESSED_PDF,
    CAROLINA_2026_SUBMITTED_PDF,
    CAROLINA_2026_XML,
    import_tz2_from_xml,
    processed_external_identifier,
)
from accounting.services.tax_forms.tz2.parse import parse_tz2_xml, parse_tz2_xml_meta
from accounting.services.tax_forms.tz2.submit import mark_tz2_submitted
from accounting.services.tax_forms.tz2.tz2_returns import create_tz2_return_draft
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant


class Tz2ReturnSubmissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='alma-tz2', name='Alma Čižmić')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Alma Čižmić',
            street='Ulica bribirskih knezova',
            house_number='9',
            postal_code='22211',
            city='Vodice',
            vat_number='20501805574',
            tax_office=tax_office,
        )
        cls.accountant = ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Ante',
            last_name='Vrcan',
        )
        User = get_user_model()
        cls.user = User.objects.create_user(username='tz2-submit-admin', password='test')

    def test_alma_draft_and_submit_does_not_lock_vat_period(self):
        period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=7, status='open')
        tz2_return = create_tz2_return_draft(
            self.tenant,
            2026,
            build_input=alma_2026_input(
                prepared_by_first_name=self.accountant.first_name,
                prepared_by_last_name=self.accountant.last_name,
            ),
            prepared_by=self.accountant,
        )
        self.assertIsInstance(tz2_return, TZ2Return)
        self.assertIsNone(getattr(tz2_return, 'vat_period_id', None))
        self.assertEqual(tz2_return.tax_year, 2026)
        self.assertEqual(tz2_return.payload_snapshot['podatak']['01'], 10)
        self.assertEqual(tz2_return.payload_snapshot['podatak']['34'], '1')
        self.assertFalse(hasattr(tz2_return.get_period(), '_meta'))
        self.assertEqual(tz2_return.get_period().year, 2026)

        submitted_at = timezone.make_aware(datetime(2026, 9, 15, 12, 0, 0))
        mark_tz2_submitted(
            tz2_return,
            submitted_at=submitted_at,
            eporezna_identifier=uuid4(),
            submitted_by=self.user,
            version_confirmed=True,
        )
        event = SubmissionService.current_submission(tz2_return)
        self.assertIsNotNone(event)
        self.assertEqual(event.document_type, TaxDocumentType.TZ2)

        period.refresh_from_db()
        self.assertEqual(period.status, 'open')
        self.assertIsNone(period.submitted_at)

    def test_attach_tz2_xml_confirmation(self):
        tz2_return = create_tz2_return_draft(
            self.tenant,
            2026,
            build_input=alma_2026_input(
                prepared_by_first_name=self.accountant.first_name,
                prepared_by_last_name=self.accountant.last_name,
            ),
            prepared_by=self.accountant,
        )
        mark_tz2_submitted(
            tz2_return,
            submitted_at=timezone.make_aware(datetime(2026, 9, 15, 12, 0, 0)),
            eporezna_identifier=uuid4(),
            submitted_by=self.user,
            version_confirmed=True,
        )
        event = SubmissionService.current_submission(tz2_return)
        xml_bytes = tz2_return.xml_unsigned.read()
        tz2_return.xml_unsigned.seek(0)
        from django.core.files.uploadedfile import SimpleUploadedFile

        uploaded = SimpleUploadedFile('TZ2_2026.xml', xml_bytes, content_type='application/xml')
        attached = SubmissionService.attach_confirmation(
            event,
            uploaded,
            uploaded_by=self.user,
        )
        self.assertTrue(attached.confirmation_attachment)
        self.assertTrue(attached.confirmation_attachment.name.endswith('.xml'))

    def test_default_input_uses_alma_oib(self):
        tz2_return = create_tz2_return_draft(self.tenant, 2026, prepared_by=self.accountant)
        self.assertEqual(tz2_return.payload_snapshot['taxpayer']['first_name'], 'ALMA')
        self.assertEqual(tz2_return.payload_snapshot['taxpayer']['last_name'], 'ČIZMIĆ')
        self.assertEqual(tz2_return.payload_snapshot['podatak']['36'], '17360.00')
        self.assertEqual(tz2_return.payload_snapshot['taxpayer']['house_number'], '0009')
        self.assertEqual(tz2_return.payload_snapshot['taxpayer']['municipality_code'], '500')

    def test_carolina_import_two_events_does_not_lock_vat_period(self):
        tenant = Tenant.objects.create(slug='carolina-tz2-import', name='Carolina Plaza')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        CompanySettings.all_objects.create(
            tenant=tenant,
            company_name='Carolina Plaza Rodriguez',
            street='Srima XVIII',
            house_number='107',
            postal_code='22211',
            city='Srima',
            vat_number='07155680871',
            tax_office=tax_office,
        )
        period = VATPeriod.all_objects.create(tenant=tenant, year=2026, month=1, status='open')
        xml_bytes = CAROLINA_2026_XML.read_bytes()
        parsed = parse_tz2_xml(xml_bytes)
        meta = parse_tz2_xml_meta(xml_bytes)
        self.assertEqual(parsed.room_beds, 8)
        self.assertEqual(parsed.aux_beds, 4)
        self.assertEqual(str(parsed.amount_after_discount), '59.72')
        self.assertEqual(parsed.installment_flag, '1')
        self.assertEqual(str(parsed.opg_aux_bed_rate), '0.00')
        self.assertEqual(str(meta.identifikator), 'ea0d211e-ee15-41e5-b0ef-36229b6632c6')

        first = import_tz2_from_xml(
            tenant,
            2026,
            xml_bytes,
            submitted_pdf_bytes=CAROLINA_2026_SUBMITTED_PDF.read_bytes(),
            processed_pdf_bytes=CAROLINA_2026_PROCESSED_PDF.read_bytes(),
            submitted_by=self.user,
        )
        self.assertTrue(first.created)
        self.assertEqual(first.tz2_return.xml_unsigned.read(), xml_bytes)
        first.tz2_return.xml_unsigned.seek(0)
        self.assertEqual(first.tz2_return.xml_submitted.read(), xml_bytes)
        first.tz2_return.xml_submitted.seek(0)
        self.assertEqual(first.tz2_return.payload_snapshot['podatak']['01'], 8)
        self.assertEqual(first.tz2_return.payload_snapshot['podatak']['04'], 4)
        self.assertEqual(first.tz2_return.payload_snapshot['podatak']['31'], '59.72')
        self.assertEqual(first.tz2_return.payload_snapshot['podatak']['34'], '1')
        self.assertEqual(first.submitted_event.source, 'import')
        self.assertEqual(
            str(first.submitted_event.external_identifier),
            'ea0d211e-ee15-41e5-b0ef-36229b6632c6',
        )
        self.assertEqual(
            first.processed_event.external_identifier,
            processed_external_identifier(meta.identifikator),
        )
        self.assertTrue(first.submitted_event.confirmation_attachment)
        self.assertTrue(first.processed_event.confirmation_attachment)
        events = list(get_submission_events(first.tz2_return))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].submission_no, 1)
        self.assertEqual(events[1].submission_no, 2)
        current = SubmissionService.current_submission(first.tz2_return)
        self.assertEqual(current.pk, first.processed_event.pk)

        second = import_tz2_from_xml(
            tenant,
            2026,
            xml_bytes,
            submitted_pdf_bytes=CAROLINA_2026_SUBMITTED_PDF.read_bytes(),
            processed_pdf_bytes=CAROLINA_2026_PROCESSED_PDF.read_bytes(),
            submitted_by=self.user,
        )
        self.assertFalse(second.created)
        self.assertEqual(second.tz2_return.pk, first.tz2_return.pk)
        self.assertEqual(len(list(get_submission_events(second.tz2_return))), 2)

        period.refresh_from_db()
        self.assertEqual(period.status, 'open')
        self.assertIsNone(period.submitted_at)
