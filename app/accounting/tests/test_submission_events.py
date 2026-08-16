"""Tests for generic SubmissionEvent architecture (ADR-0008, ADR-0009)."""

from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounting.models import (
    PDVSReturn,
    SubmissionDestination,
    SubmissionEvent,
    SubmissionEventState,
    SubmissionSource,
    TaxDocumentType,
    VATPeriod,
    VATReturn,
    VATReturnStatus,
)
from accounting.services.submission.events import (
    CreateSubmissionEventError,
    DuplicateExternalIdentifierError,
    TransitionSubmissionStateError,
    create_submission_event,
    transition_submission_state,
)
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.submit import mark_vat_return_submitted
from accounting.services.tax_forms.pdv.vat_returns import create_vat_return_draft
from accounting.services.tax_forms.pdv_s.submit import mark_pdv_s_submitted
from accounting.tests.test_pdv_checkpoint_april_2026 import PdvCheckpointApril2026Tests
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice


class SubmissionEventServiceTests(TestCase):
    def setUp(self):
        from tenants.models import Tenant

        self.tenant = Tenant.objects.create(slug='submission-events', name='Submission events test')
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
        self.user = User.objects.create_user(username='submission-admin', password='test')
        self.pdv_s_return = PDVSReturn.objects.create(
            tenant=self.tenant,
            vat_period=self.period,
            version=1,
        )
        self.submitted_at = datetime(2026, 7, 6, 11, 59, 38, tzinfo=ZoneInfo('Europe/Zagreb'))

    def _create_event(self, external_id=None, **kwargs):
        defaults = {
            'document': self.pdv_s_return,
            'destination': SubmissionDestination.EPOREZNA,
            'external_identifier': external_id or uuid4(),
            'submitted_at': self.submitted_at,
            'submitted_by': self.user,
            'submission_method': SubmissionSource.MANUAL,
            'version_confirmed': True,
        }
        defaults.update(kwargs)
        return create_submission_event(**defaults)

    def test_pdv_s_initial_event(self):
        ep_uuid = UUID('3d50e215-cf30-48a9-93c0-b68da8182ecb')
        event = self._create_event(external_id=ep_uuid)

        self.assertEqual(event.submission_no, 1)
        self.assertEqual(event.state, SubmissionEventState.SUBMITTED)
        self.assertEqual(event.destination, SubmissionDestination.EPOREZNA)
        self.assertEqual(event.external_identifier, ep_uuid)
        self.assertEqual(event.submission_type, 'initial')
        self.assertEqual(event.document_type, TaxDocumentType.PDV_S)
        self.assertIsNotNone(event.event_uuid)
        self.assertEqual(len(event.payload_hash), 64)

    def test_pdv_s_correction_chain(self):
        first = self._create_event(external_id=uuid4())
        second = self._create_event(external_id=uuid4())
        third = self._create_event(external_id=uuid4())

        self.assertEqual(second.submission_no, 2)
        self.assertEqual(second.supersedes_submission_id, first.pk)
        self.assertEqual(third.supersedes_submission_id, second.pk)
        self.assertEqual(third.submission_type, 'correction')

    def test_duplicate_external_id_per_destination(self):
        ep_uuid = uuid4()
        self._create_event(external_id=ep_uuid)
        with self.assertRaises(DuplicateExternalIdentifierError):
            self._create_event(external_id=ep_uuid)

    def test_state_transition_pending_to_submitted(self):
        event = self._create_event(
            external_id=uuid4(),
            state=SubmissionEventState.PENDING,
        )
        transitioned = transition_submission_state(
            event,
            SubmissionEventState.SUBMITTED,
            transitioned_by=self.user,
        )
        self.assertEqual(transitioned.state, SubmissionEventState.SUBMITTED)

    def test_state_transition_rejects_invalid(self):
        event = self._create_event(external_id=uuid4())
        with self.assertRaises(TransitionSubmissionStateError):
            transition_submission_state(
                event,
                SubmissionEventState.PENDING,
                transitioned_by=self.user,
            )

    def test_immutable_core_fields(self):
        event = self._create_event(external_id=uuid4())
        event.external_identifier = uuid4()
        with self.assertRaises(ValidationError):
            event.save()

    def test_immutable_state_without_service(self):
        event = self._create_event(external_id=uuid4())
        event.state = SubmissionEventState.REJECTED
        with self.assertRaises(ValidationError):
            event.save()

    def test_active_submission(self):
        first = self._create_event(external_id=uuid4())
        second = self._create_event(external_id=uuid4())
        active = SubmissionService.current_submission(self.pdv_s_return)
        self.assertEqual(active.pk, second.pk)
        self.assertNotEqual(active.pk, first.pk)

    def test_requires_version_confirmed(self):
        with self.assertRaises(CreateSubmissionEventError):
            create_submission_event(
                self.pdv_s_return,
                destination=SubmissionDestination.EPOREZNA,
                external_identifier=uuid4(),
                submitted_at=self.submitted_at,
                submitted_by=self.user,
                submission_method=SubmissionSource.MANUAL,
                version_confirmed=False,
            )


class PdvSubmissionEventIntegrationTests(PdvCheckpointApril2026Tests):
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
        User = get_user_model()
        cls.user = User.objects.create_user(username='pdv-event-admin', password='test')

    def test_pdv_initial_submission_event(self):
        vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)
        submitted_at = timezone.make_aware(datetime(2026, 7, 6, 11, 54, 46))
        ep_uuid = UUID('f5e5437e-0a31-4d8e-8c9a-8275541f082d')

        result = mark_vat_return_submitted(
            vat_return,
            submitted_at=submitted_at,
            eporezna_identifier=ep_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )

        self.assertEqual(result.status, VATReturnStatus.SUBMITTED)
        event = SubmissionService.current_submission(result)
        self.assertIsNotNone(event)
        self.assertEqual(event.submission_no, 1)
        self.assertEqual(event.external_identifier, ep_uuid)
        self.assertEqual(event.destination, SubmissionDestination.EPOREZNA)
        self.assertEqual(event.source, SubmissionSource.MANUAL)
        self.assertEqual(event.payload_hash, vat_return.payload_hash)

    def test_pdv_s_mark_submitted_creates_event(self):
        may_period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=5)
        submitted_at = datetime(2026, 7, 6, 11, 59, 38, tzinfo=ZoneInfo('Europe/Zagreb'))
        ep_uuid = UUID('3d50e215-cf30-48a9-93c0-b68da8182ecb')

        event = mark_pdv_s_submitted(
            may_period,
            submitted_at=submitted_at,
            eporezna_identifier=ep_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )

        self.assertEqual(event.document_type, TaxDocumentType.PDV_S)
        self.assertEqual(event.external_identifier, ep_uuid)
        pdv_s_return = PDVSReturn.all_objects.get(vat_period=may_period)
        self.assertEqual(event.object_id, pdv_s_return.pk)

    def test_vat_return_has_no_submission_columns(self):
        vat_return = VATReturn.all_objects.first()
        if vat_return is None:
            vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)
        field_names = {field.name for field in VATReturn._meta.get_fields()}
        for removed in (
            'eporezna_identifier',
            'submitted_at',
            'submitted_by',
            'submission_method',
            'submission_confirmation',
            'submitted_version',
        ):
            self.assertNotIn(removed, field_names)

    def test_pdvssubmission_model_removed(self):
        from django.apps import apps

        with self.assertRaises(LookupError):
            apps.get_model('accounting', 'PdvSSubmission')
