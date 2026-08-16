"""Tests for SubmissionService (ADR-0009)."""

from datetime import datetime
from io import BytesIO
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounting.models import (
    PDVSReturn,
    SubmissionDestination,
    SubmissionEvent,
    SubmissionEventState,
    SubmissionSource,
    TaxDocumentType,
    VATPeriod,
)
from accounting.services.submission.exceptions import (
    AttachConfirmationError,
    DuplicateExternalIdentifierError,
    SupersedeSubmissionError,
)
from accounting.services.submission.service import SubmissionService
from accounting.services.submission.events import transition_submission_state
from accounting.services.tax_forms.zp.zp_returns import create_zp_return_draft
from accounting.tests.fixtures.tax.zp.load import load_scenario
from accounting.tests.test_zp_aggregate import _seed_ledger
from settings.models import CompanySettings, TaxOffice


class SubmissionServiceTests(TestCase):
    def setUp(self):
        from tenants.models import Tenant

        self.tenant = Tenant.objects.create(slug='submission-svc', name='Submission service test')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        CompanySettings.all_objects.create(
            tenant=self.tenant,
            company_name='Fine Star d.o.o.',
            company_address='Test',
            street='Test',
            house_number='1',
            postal_code='22000',
            city='Šibenik',
            company_phone='+385 22 000 000',
            company_email='info@finestar.example',
            vat_number='36619131370',
            tax_office=tax_office,
        )
        self.period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=5)
        User = get_user_model()
        self.user = User.objects.create_user(username='submission-svc-admin', password='test')
        self.pdv_s_return = PDVSReturn.objects.create(
            tenant=self.tenant,
            vat_period=self.period,
            version=1,
        )
        self.submitted_at = datetime(2026, 7, 6, 11, 59, 38, tzinfo=ZoneInfo('Europe/Zagreb'))

    def _create_event(self, external_id=None, **kwargs):
        defaults = {
            'destination': SubmissionDestination.EPOREZNA,
            'external_identifier': external_id or uuid4(),
            'submitted_at': self.submitted_at,
            'submitted_by': self.user,
            'source': SubmissionSource.MANUAL,
            'version_confirmed': True,
        }
        defaults.update(kwargs)
        return SubmissionService.create_event(self.pdv_s_return, **defaults)

    def test_event_has_uuid_and_payload_hash(self):
        event = self._create_event()
        self.assertIsNotNone(event.event_uuid)
        self.assertEqual(len(event.payload_hash), 64)
        self.assertEqual(event.source, SubmissionSource.MANUAL)

    def test_document_type_derived_from_content_type(self):
        event = self._create_event()
        self.assertEqual(event.document_type, TaxDocumentType.PDV_S)

    def test_supersede_chain(self):
        first = self._create_event(external_id=uuid4())
        second = SubmissionService.supersede(
            self.pdv_s_return,
            previous_event=first,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=uuid4(),
            submitted_at=self.submitted_at,
            submitted_by=self.user,
            source=SubmissionSource.MANUAL,
            version_confirmed=True,
        )
        self.assertEqual(second.submission_no, 2)
        self.assertEqual(second.supersedes_submission_id, first.pk)
        self.assertEqual(second.submission_type, 'correction')

    def test_supersede_rejects_non_active(self):
        first = self._create_event(external_id=uuid4())
        SubmissionService.supersede(
            self.pdv_s_return,
            previous_event=first,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=uuid4(),
            submitted_at=self.submitted_at,
            submitted_by=self.user,
            source=SubmissionSource.MANUAL,
            version_confirmed=True,
        )
        with self.assertRaises(SupersedeSubmissionError):
            SubmissionService.supersede(
                self.pdv_s_return,
                previous_event=first,
                destination=SubmissionDestination.EPOREZNA,
                external_identifier=uuid4(),
                submitted_at=self.submitted_at,
                submitted_by=self.user,
                source=SubmissionSource.MANUAL,
                version_confirmed=True,
            )

    def test_current_submission(self):
        first = self._create_event(external_id=uuid4())
        second = self._create_event(external_id=uuid4())
        active = SubmissionService.current_submission(self.pdv_s_return)
        self.assertEqual(active.pk, second.pk)
        self.assertNotEqual(active.pk, first.pk)

    def test_duplicate_external_identifier(self):
        ep_uuid = uuid4()
        self._create_event(external_id=ep_uuid)
        with self.assertRaises(DuplicateExternalIdentifierError):
            self._create_event(external_id=ep_uuid)

    def test_immutable_core_fields(self):
        event = self._create_event(external_id=uuid4())
        event.external_identifier = uuid4()
        with self.assertRaises(ValidationError):
            event.save()

    def test_immutable_event_uuid(self):
        event = self._create_event(external_id=uuid4())
        event.event_uuid = uuid4()
        with self.assertRaises(ValidationError):
            event.save()

    def test_attach_pdf_succeeds(self):
        event = self._create_event(external_id=uuid4())
        pdf = SimpleUploadedFile('potvrda.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        updated = SubmissionService.attach_confirmation(event, pdf, uploaded_by=self.user)
        self.assertTrue(updated.confirmation_attachment.name.endswith('.pdf'))

    def test_attach_rejects_second_file(self):
        event = self._create_event(external_id=uuid4())
        pdf = SimpleUploadedFile('potvrda.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        SubmissionService.attach_confirmation(event, pdf, uploaded_by=self.user)
        with self.assertRaises(AttachConfirmationError):
            SubmissionService.attach_confirmation(
                event,
                SimpleUploadedFile('other.pdf', b'%PDF-1.4 other', content_type='application/pdf'),
                uploaded_by=self.user,
            )

    def test_attach_rejects_invalid_extension(self):
        event = self._create_event(external_id=uuid4())
        bad = SimpleUploadedFile('virus.exe', b'bad', content_type='application/octet-stream')
        with self.assertRaises(AttachConfirmationError):
            SubmissionService.attach_confirmation(event, bad, uploaded_by=self.user)

    def test_state_transition_via_service(self):
        event = self._create_event(
            external_id=uuid4(),
            state=SubmissionEventState.PENDING,
        )
        transitioned = SubmissionService.transition_state(
            event,
            SubmissionEventState.SUBMITTED,
            transitioned_by=self.user,
        )
        self.assertEqual(transitioned.state, SubmissionEventState.SUBMITTED)

    def test_validate_xml_without_side_effects(self):
        event = self._create_event(external_id=uuid4())
        xml = b'<?xml version="1.0"?><root/>'
        file_obj = SimpleUploadedFile('bad.xml', xml, content_type='application/xml')
        result = SubmissionService.validate(file_obj, document=self.pdv_s_return, event=event)
        self.assertFalse(result.valid)
        self.assertTrue(result.errors)


class SubmissionServiceZpTests(TestCase):
    def setUp(self):
        from tenants.models import Tenant

        self.tenant = Tenant.objects.create(slug='submission-zp', name='Submission ZP test')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        CompanySettings.all_objects.create(
            tenant=self.tenant,
            company_name='Fine Star d.o.o.',
            company_address='Test',
            street='Test',
            house_number='1',
            postal_code='22000',
            city='Šibenik',
            company_phone='+385 22 000 000',
            company_email='info@finestar.example',
            vat_number='36619131370',
            tax_office=tax_office,
        )
        data = load_scenario('single_eu_partner')
        period_info = data['ledger']['period']
        self.period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )
        _seed_ledger(tenant=self.tenant, period=self.period, ledger=data['ledger'])
        User = get_user_model()
        self.user = User.objects.create_user(username='submission-zp-admin', password='test')
        self.zp_return = create_zp_return_draft(self.period)
        self.submitted_at = datetime(2026, 7, 6, 11, 59, 38, tzinfo=ZoneInfo('Europe/Zagreb'))

    def _create_event(self, external_id=None, **kwargs):
        defaults = {
            'destination': SubmissionDestination.EPOREZNA,
            'external_identifier': external_id or uuid4(),
            'submitted_at': self.submitted_at,
            'submitted_by': self.user,
            'source': SubmissionSource.MANUAL,
            'version_confirmed': True,
        }
        defaults.update(kwargs)
        return SubmissionService.create_event(self.zp_return, **defaults)

    def test_event_has_uuid_and_payload_hash(self):
        event = self._create_event()
        self.assertIsNotNone(event.event_uuid)
        self.assertEqual(len(event.payload_hash), 64)
        self.assertEqual(event.payload_hash, self.zp_return.payload_hash)

    def test_document_type_derived_from_content_type(self):
        event = self._create_event()
        self.assertEqual(event.document_type, TaxDocumentType.ZP)

    def test_supersede_chain(self):
        first = self._create_event(external_id=uuid4())
        second = SubmissionService.supersede(
            self.zp_return,
            previous_event=first,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=uuid4(),
            submitted_at=self.submitted_at,
            submitted_by=self.user,
            source=SubmissionSource.MANUAL,
            version_confirmed=True,
        )
        self.assertEqual(second.submission_no, 2)
        self.assertEqual(second.supersedes_submission_id, first.pk)
        self.assertEqual(second.submission_type, 'correction')

    def test_validate_zp_xml_without_side_effects(self):
        event = self._create_event(external_id=uuid4())
        xml = b'<?xml version="1.0"?><root/>'
        file_obj = SimpleUploadedFile('bad.xml', xml, content_type='application/xml')
        result = SubmissionService.validate(file_obj, document=self.zp_return, event=event)
        self.assertFalse(result.valid)
        self.assertTrue(result.errors)
