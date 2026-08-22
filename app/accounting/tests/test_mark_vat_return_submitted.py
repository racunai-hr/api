"""Tests for mark_vat_return_submitted and mark_pdv_s_submitted."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounting.models import (
    PDVSReturn,
    SubmissionDestination,
    SubmissionSource,
    TaxDocumentType,
    VATLedgerEntry,
    VATPeriod,
    VATReturnStatus,
)
from accounting.services.submission.protocol import pdv_s_payload_hash_from_aggregate
from accounting.services.tax_forms.pdv_s.aggregate import aggregate_pdv_s_rows
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.submit import (
    MarkVatReturnSubmittedError,
    mark_vat_return_submitted,
)
from accounting.services.tax_forms.pdv.vat_returns import create_vat_return_draft
from accounting.services.tax_forms.pdv_s.submit import MarkPdvSSubmittedError, mark_pdv_s_submitted
from accounting.tests.test_pdv_checkpoint_april_2026 import PdvCheckpointApril2026Tests
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice


class MarkVatReturnSubmittedTests(PdvCheckpointApril2026Tests):
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
        cls.user = User.objects.create_user(username='submit-admin', password='test')

    def test_happy_path(self):
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
        self.assertEqual(event.source, SubmissionSource.MANUAL)
        self.assertEqual(event.external_identifier, ep_uuid)
        self.assertEqual(event.submitted_by, self.user)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'submitted')
        self.assertEqual(self.period.submitted_at, submitted_at)

    def test_requires_version_confirmed(self):
        vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)
        with self.assertRaises(MarkVatReturnSubmittedError):
            mark_vat_return_submitted(
                vat_return,
                submitted_at=timezone.now(),
                eporezna_identifier=uuid4(),
                submitted_by=self.user,
                version_confirmed=False,
            )
        vat_return.refresh_from_db()
        self.assertEqual(vat_return.status, VATReturnStatus.GENERATED)

    def test_pdv_s_submit_leaves_period_open_for_pdv_draft_then_pdv_locks(self):
        mark_pdv_s_submitted(
            self.period,
            submitted_at=datetime(2026, 8, 6, 13, 5, 8, tzinfo=ZoneInfo('Europe/Zagreb')),
            eporezna_identifier=UUID('2c36debd-393a-40e6-b002-d172a2ac1ba5'),
            submitted_by=self.user,
            version_confirmed=True,
        )
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'open')

        vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)
        self.assertEqual(vat_return.status, VATReturnStatus.GENERATED)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'open')

        mark_vat_return_submitted(
            vat_return,
            submitted_at=timezone.now(),
            eporezna_identifier=uuid4(),
            submitted_by=self.user,
            version_confirmed=True,
        )
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'submitted')

    def test_rejects_already_submitted(self):
        vat_return = create_vat_return_draft(self.period, prepared_by=self.accountant)
        mark_vat_return_submitted(
            vat_return,
            submitted_at=timezone.now(),
            eporezna_identifier=uuid4(),
            submitted_by=self.user,
            version_confirmed=True,
        )
        with self.assertRaises(MarkVatReturnSubmittedError):
            mark_vat_return_submitted(
                vat_return,
                submitted_at=timezone.now(),
                eporezna_identifier=uuid4(),
                submitted_by=self.user,
                version_confirmed=True,
            )


class MarkPdvSSubmittedTests(TestCase):
    def setUp(self):
        from tenants.models import Tenant

        self.tenant = Tenant.objects.create(slug='pdv-s-submit', name='PDV-S submit test')
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
        self.user = User.objects.create_user(username='pdv-s-admin', password='test')

    def _eu_goods(self, *, document_number: str, partner_oib: str, amount: str, day: int = 10):
        return VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=self.period,
            ledger_type=VATLedgerEntry.LEDGER_U_RA,
            entry_date=date(2026, 5, day),
            document_number=document_number,
            partner_name='EU Supplier',
            partner_oib=partner_oib,
            base_amount=Decimal(amount),
            vat_rate=Decimal('0.00'),
            vat_amount=Decimal('0.00'),
            vat_box='207',
        )

    def test_happy_path(self):
        submitted_at = datetime(2026, 7, 6, 11, 59, 38, tzinfo=ZoneInfo('Europe/Zagreb'))
        ep_uuid = UUID('3d50e215-cf30-48a9-93c0-b68da8182ecb')

        event = mark_pdv_s_submitted(
            self.period,
            submitted_at=submitted_at,
            eporezna_identifier=ep_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )

        self.assertEqual(event.external_identifier, ep_uuid)
        self.assertEqual(event.source, SubmissionSource.MANUAL)
        self.assertEqual(event.submitted_by, self.user)
        self.assertEqual(event.document_type, TaxDocumentType.PDV_S)
        self.assertTrue(PDVSReturn.all_objects.filter(vat_period=self.period).exists())
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'open')
        self.assertIsNone(self.period.submitted_at)

    def test_allows_correction_chain(self):
        first_uuid = uuid4()
        second_uuid = uuid4()
        mark_pdv_s_submitted(
            self.period,
            submitted_at=timezone.now(),
            eporezna_identifier=first_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )
        second = mark_pdv_s_submitted(
            self.period,
            submitted_at=timezone.now(),
            eporezna_identifier=second_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )
        self.assertEqual(second.submission_no, 2)
        self.assertEqual(second.submission_type, 'correction')

    def test_correction_freezes_distinct_payload_hashes(self):
        self._eu_goods(document_number='EU-A', partner_oib='DE123456789', amount='33000.00')
        hash_a = pdv_s_payload_hash_from_aggregate(aggregate_pdv_s_rows(self.period))
        first = mark_pdv_s_submitted(
            self.period,
            submitted_at=timezone.now(),
            eporezna_identifier=uuid4(),
            submitted_by=self.user,
            version_confirmed=True,
        )
        self.assertEqual(first.submission_no, 1)
        self.assertEqual(first.payload_hash, hash_a)

        self._eu_goods(
            document_number='EU-B',
            partner_oib='DE355497142',
            amount='10000.00',
            day=20,
        )
        hash_b = pdv_s_payload_hash_from_aggregate(aggregate_pdv_s_rows(self.period))
        self.assertNotEqual(hash_a, hash_b)

        second = mark_pdv_s_submitted(
            self.period,
            submitted_at=timezone.now(),
            eporezna_identifier=uuid4(),
            submitted_by=self.user,
            version_confirmed=True,
        )
        first.refresh_from_db()
        self.assertEqual(second.submission_no, 2)
        self.assertEqual(second.submission_type, 'correction')
        self.assertEqual(second.payload_hash, hash_b)
        self.assertEqual(first.payload_hash, hash_a)
        self.assertNotEqual(first.payload_hash, second.payload_hash)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'open')

    def test_rejects_duplicate_external_id(self):
        shared_uuid = uuid4()
        mark_pdv_s_submitted(
            self.period,
            submitted_at=timezone.now(),
            eporezna_identifier=shared_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )
        other_period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=6)
        with self.assertRaises(MarkPdvSSubmittedError):
            mark_pdv_s_submitted(
                other_period,
                submitted_at=timezone.now(),
                eporezna_identifier=shared_uuid,
                submitted_by=self.user,
                version_confirmed=True,
            )

    def test_requires_version_confirmed(self):
        with self.assertRaises(MarkPdvSSubmittedError):
            mark_pdv_s_submitted(
                self.period,
                submitted_at=timezone.now(),
                eporezna_identifier=uuid4(),
                submitted_by=self.user,
                version_confirmed=False,
            )
