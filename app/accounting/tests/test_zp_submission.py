"""Tests for mark_zp_submitted and ZP submission correction chain."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounting.models import (
    SubmissionDestination,
    SubmissionSource,
    TaxDocumentType,
    VATLedgerEntry,
    VATPeriod,
)
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.zp.submit import MarkZpSubmittedError, mark_zp_submitted
from accounting.services.tax_forms.zp.zp_returns import create_zp_return_draft
from accounting.tests.fixtures.tax.zp.load import load_scenario
from accounting.tests.test_zp_aggregate import _seed_ledger
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant


class MarkZpSubmittedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='zp-submit', name='ZP Submit Co')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='FINE STAR D.O.O.',
            street='Bana Josipa Jelačića',
            house_number='58',
            postal_code='22000',
            city='Šibenik',
            vat_number='36619131370',
            tax_office=tax_office,
        )
        cls.accountant = ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Toni',
            last_name='Šupe',
        )
        User = get_user_model()
        cls.user = User.objects.create_user(username='zp-submit-admin', password='test')

    def _setup_period_with_ledger(self, scenario_id: str = 'single_eu_partner'):
        data = load_scenario(scenario_id)
        ledger = data['ledger']
        period_info = ledger['period']
        period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )
        _seed_ledger(tenant=self.tenant, period=period, ledger=ledger)
        return period, data

    def test_happy_path(self):
        period, _ = self._setup_period_with_ledger()
        zp_return = create_zp_return_draft(period, prepared_by=self.accountant)
        submitted_at = timezone.make_aware(datetime(2026, 7, 6, 11, 54, 46))
        ep_uuid = UUID('f5e5437e-0a31-4d8e-8c9a-8275541f082d')

        result = mark_zp_submitted(
            zp_return,
            submitted_at=submitted_at,
            eporezna_identifier=ep_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )

        event = SubmissionService.current_submission(result)
        self.assertIsNotNone(event)
        self.assertEqual(event.source, SubmissionSource.MANUAL)
        self.assertEqual(event.external_identifier, ep_uuid)
        self.assertEqual(event.submitted_by, self.user)
        self.assertEqual(event.document_type, TaxDocumentType.ZP)
        period.refresh_from_db()
        self.assertEqual(period.status, 'submitted')
        self.assertEqual(period.submitted_at, submitted_at)

    def test_correction_chain(self):
        data = load_scenario('period_correction')
        period_info = data['ledger_v1']['period']
        period = VATPeriod.all_objects.create(
            tenant=self.tenant,
            year=period_info['year'],
            month=period_info['month'],
        )
        _seed_ledger(tenant=self.tenant, period=period, ledger=data['ledger_v1'])
        v1 = create_zp_return_draft(period, prepared_by=self.accountant)
        first_uuid = uuid4()
        mark_zp_submitted(
            v1,
            submitted_at=timezone.now(),
            eporezna_identifier=first_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )
        first_event = SubmissionService.current_submission(v1)
        self.assertEqual(first_event.submission_no, 1)
        self.assertEqual(first_event.submission_type, 'initial')

        VATLedgerEntry.all_objects.filter(vat_period=period).delete()
        _seed_ledger(tenant=self.tenant, period=period, ledger=data['ledger_v2'])
        v2 = create_zp_return_draft(period, prepared_by=self.accountant)
        self.assertEqual(v2.version, 2)
        mark_zp_submitted(
            v2,
            submitted_at=timezone.now(),
            eporezna_identifier=uuid4(),
            submitted_by=self.user,
            version_confirmed=True,
        )
        initial_v2 = SubmissionService.current_submission(v2)
        self.assertEqual(initial_v2.submission_no, 1)

        second = SubmissionService.supersede(
            v2,
            previous_event=initial_v2,
            destination=SubmissionDestination.EPOREZNA,
            external_identifier=uuid4(),
            submitted_at=timezone.now(),
            submitted_by=self.user,
            source=SubmissionSource.MANUAL,
            version_confirmed=True,
        )
        self.assertEqual(second.submission_no, 2)
        self.assertEqual(second.submission_type, 'correction')
        self.assertEqual(second.supersedes_submission_id, initial_v2.pk)

    def test_rejects_duplicate_external_id(self):
        period, _ = self._setup_period_with_ledger()
        zp_return = create_zp_return_draft(period, prepared_by=self.accountant)
        shared_uuid = uuid4()
        mark_zp_submitted(
            zp_return,
            submitted_at=timezone.now(),
            eporezna_identifier=shared_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )
        other_period = VATPeriod.all_objects.create(tenant=self.tenant, year=2026, month=7)
        data = load_scenario('multiple_eu_partners')
        _seed_ledger(tenant=self.tenant, period=other_period, ledger=data['ledger'])
        other_return = create_zp_return_draft(other_period, prepared_by=self.accountant)
        with self.assertRaises(MarkZpSubmittedError):
            mark_zp_submitted(
                other_return,
                submitted_at=timezone.now(),
                eporezna_identifier=shared_uuid,
                submitted_by=self.user,
                version_confirmed=True,
            )

    def test_requires_version_confirmed(self):
        period, _ = self._setup_period_with_ledger()
        zp_return = create_zp_return_draft(period, prepared_by=self.accountant)
        with self.assertRaises(MarkZpSubmittedError):
            mark_zp_submitted(
                zp_return,
                submitted_at=timezone.now(),
                eporezna_identifier=uuid4(),
                submitted_by=self.user,
                version_confirmed=False,
            )
        self.assertIsNone(SubmissionService.current_submission(zp_return))
