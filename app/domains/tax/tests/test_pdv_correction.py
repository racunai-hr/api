"""PDV correction lifecycle — ADR-0027."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from lxml import etree
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import (
    SubmissionEvent,
    VATLedgerEntry,
    VATPeriod,
    VATReturn,
    VATReturnStatus,
)
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.build import build_pdv_payload
from accounting.services.tax_forms.pdv.correction import (
    CORRECTION_ALREADY_IN_PROGRESS,
    PreparePdvCorrectionError,
    prepare_pdv_correction,
)
from accounting.services.tax_forms.pdv.submit import mark_vat_return_submitted
from accounting.services.tax_forms.pdv.validation import PdvSchemaValidationError
from accounting.services.tax_forms.pdv.vat_returns import create_vat_return_draft
from accounting.services.tax_projection.rebuild import RebuildOutcome, rebuild_vat_ledger
from expenses.models import Expense, ExpenseCategory
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant, TenantMembership

HOST = 'pdvcorr.racunai.hr'
_APRIL_SIGNED_PDV = (
    Path(__file__).resolve().parents[3]
    / 'accounting'
    / 'tests'
    / 'fixtures'
    / 'pdv'
    / 'submitted_april_2026.xml'
)
_METADATA_NS = 'http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0'


def _rewrite_signed_pdv_xml(*, oib: str, period_from: str, period_to: str) -> bytes:
    xml = _APRIL_SIGNED_PDV.read_bytes()
    xml = xml.replace(b'<OIB>36619131370</OIB>', f'<OIB>{oib}</OIB>'.encode())
    xml = xml.replace(b'<DatumOd>2026-04-01</DatumOd>', f'<DatumOd>{period_from}</DatumOd>'.encode())
    xml = xml.replace(b'<DatumDo>2026-04-30</DatumDo>', f'<DatumDo>{period_to}</DatumDo>'.encode())
    return xml


def _xml_document_uuid(xml_bytes: bytes) -> str:
    identifier = etree.fromstring(xml_bytes).find(f'.//{{{_METADATA_NS}}}Identifikator')
    return identifier.text.strip()


class PdvCorrectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='pdvcorr', name='PDV Correction Co')
        User = get_user_model()
        cls.user = User.objects.create_user(username='pdvcorr-acc', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='accountant')
        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Correction d.o.o.',
            company_address='Ulica 1\n10000 Zagreb',
            street='Ulica',
            house_number='1',
            postal_code='10000',
            city='Zagreb',
            company_phone='+385 1 000 000',
            company_email='corr@example.com',
            vat_number='12345678901',
            tax_office=tax_office,
        )
        cls.accountant = ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Ana',
            last_name='Ispravak',
        )
        cls.hr_customer = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Kupac RH',
            tax_number='11111111111',
            partner_type='customer',
            status='active',
            address='',
            city='',
            postal_code='',
            country='Croatia',
        )
        cls.nl_customer = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Asenova',
            tax_number='NL123456789B01',
            vat_number='NL123456789B01',
            partner_type='customer',
            status='active',
            address='',
            city='',
            postal_code='',
            country='Netherlands',
            country_code='NL',
        )
        cls.hr_supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='CVH',
            tax_number='22222222222',
            partner_type='supplier',
            status='active',
            address='',
            city='',
            postal_code='',
            country='Croatia',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Servis')

    def _domestic_invoice(self):
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.hr_customer,
            invoice_number='2026-0001',
            issue_date=date(2026, 7, 10),
            due_date=date(2026, 7, 24),
            status='sent',
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Usluga RH',
            quantity=Decimal('1'),
            unit_price=Decimal('3360.00'),
            tax_rate=Decimal('25.00'),
        )
        invoice.recalculate_totals()
        return invoice

    def _eu_service_invoice(self):
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            company_to=self.nl_customer,
            invoice_number='INV-001/2026',
            issue_date=date(2026, 7, 27),
            due_date=date(2026, 8, 10),
            service_date=date(2026, 7, 27),
            status='sent',
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='EU usluga',
            quantity=Decimal('1'),
            unit_price=Decimal('25000.00'),
            tax_rate=Decimal('0.00'),
        )
        invoice.recalculate_totals()
        return invoice

    def _hr_expense(self):
        return Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-2026-0013',
            status='paid',
            category=self.category,
            supplier=self.hr_supplier,
            amount=Decimal('82.95'),
            tax_amount=Decimal('16.59'),
            expense_date=date(2026, 7, 15),
            description='CVH',
            created_by=self.user,
        )

    def _submit_v1(self):
        self._domestic_invoice()
        result = rebuild_vat_ledger(self.tenant, 2026, 7, actor=self.user, replace=True)
        self.assertTrue(result.ok)
        period = VATPeriod.all_objects.get(tenant=self.tenant, year=2026, month=7)
        vat_return = create_vat_return_draft(period, prepared_by=self.accountant)
        portal_uuid = uuid4()
        mark_vat_return_submitted(
            vat_return,
            submitted_at=timezone.make_aware(datetime(2026, 8, 10, 12, 0, 0)),
            eporezna_identifier=portal_uuid,
            submitted_by=self.user,
            version_confirmed=True,
        )
        period.refresh_from_db()
        return period, vat_return, portal_uuid

    def test_public_rebuild_still_409_on_submitted(self):
        period, _, _ = self._submit_v1()
        self.assertEqual(period.status, 'submitted')
        result = rebuild_vat_ledger(self.tenant, 2026, 7, actor=self.user, replace=True)
        self.assertEqual(result.outcome, RebuildOutcome.NOT_WRITABLE)

    def test_prepare_correction_is_atomic_and_keeps_period_submitted(self):
        period, v1, _ = self._submit_v1()
        v1_hash = v1.payload_hash
        v1_xml = v1.xml_unsigned.open('rb').read()
        self._eu_service_invoice()
        self._hr_expense()

        payload = prepare_pdv_correction(period, actor=self.user)
        period.refresh_from_db()
        v1.refresh_from_db()

        self.assertEqual(period.status, 'submitted')
        self.assertEqual(payload['return_status'], VATReturnStatus.GENERATED)
        self.assertEqual(payload['return_version'], 2)
        self.assertEqual(v1.status, VATReturnStatus.SUBMITTED)
        self.assertEqual(v1.payload_hash, v1_hash)
        self.assertEqual(v1.xml_unsigned.open('rb').read(), v1_xml)
        self.assertEqual(period.current_return, v1)
        self.assertEqual(period.latest_return.version, 2)

        built = build_pdv_payload(period)
        self.assertEqual(built.field_scalar('103'), Decimal('25000.00'))
        self.assertEqual(built.field_scalar('111'), Decimal('25000.00'))
        self.assertEqual(built.field_scalar('000'), Decimal('28360.00'))
        self.assertEqual(built.field_pair('303').porez, Decimal('16.59'))
        self.assertEqual(built.field_scalar('400'), Decimal('823.41'))

    def test_second_prepare_returns_already_in_progress(self):
        period, _, _ = self._submit_v1()
        self._eu_service_invoice()
        prepare_pdv_correction(period, actor=self.user)
        count = VATReturn.all_objects.filter(vat_period=period).count()
        with self.assertRaises(PreparePdvCorrectionError) as ctx:
            prepare_pdv_correction(period, actor=self.user)
        self.assertEqual(ctx.exception.code, CORRECTION_ALREADY_IN_PROGRESS)
        self.assertEqual(VATReturn.all_objects.filter(vat_period=period).count(), count)

    def test_draft_failure_rolls_back_ledger(self):
        period, _, _ = self._submit_v1()
        before = list(
            VATLedgerEntry.all_objects.filter(vat_period=period)
            .order_by('pk')
            .values_list('vat_box', 'base_amount', 'vat_amount')
        )
        self._eu_service_invoice()
        with patch(
            'accounting.services.tax_forms.pdv.vat_returns.validate_pdv_obrazac_xml',
            side_effect=PdvSchemaValidationError('XSD failed'),
        ):
            with self.assertRaises(PreparePdvCorrectionError):
                prepare_pdv_correction(period, actor=self.user)
        after = list(
            VATLedgerEntry.all_objects.filter(vat_period=period)
            .order_by('pk')
            .values_list('vat_box', 'base_amount', 'vat_amount')
        )
        self.assertEqual(after, before)
        self.assertEqual(VATReturn.all_objects.filter(vat_period=period).count(), 1)
        period.refresh_from_db()
        self.assertEqual(period.status, 'submitted')

    def test_submit_correction_supersedes_v1_and_keeps_event_a(self):
        period, v1, uuid_a = self._submit_v1()
        event_a = SubmissionService.current_submission(v1)
        self._eu_service_invoice()
        self._hr_expense()
        prepare_pdv_correction(period, actor=self.user)
        v2 = period.latest_return
        uuid_b = uuid4()
        mark_vat_return_submitted(
            v2,
            submitted_at=timezone.make_aware(datetime(2026, 8, 20, 9, 0, 0)),
            eporezna_identifier=uuid_b,
            submitted_by=self.user,
            version_confirmed=True,
        )
        v1.refresh_from_db()
        v2.refresh_from_db()
        event_a.refresh_from_db()
        event_b = SubmissionService.current_submission(v2)

        self.assertEqual(v1.status, VATReturnStatus.SUPERSEDED)
        self.assertEqual(v1.superseded_by_id, v2.pk)
        self.assertEqual(v2.status, VATReturnStatus.SUBMITTED)
        self.assertEqual(event_a.external_identifier, uuid_a)
        self.assertIsNone(event_b.supersedes_submission_id)
        self.assertNotEqual(event_a.pk, event_b.pk)
        self.assertEqual(SubmissionEvent.all_objects.filter(object_id=v1.pk).count(), 1)


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class PdvCorrectionApiTests(PdvCorrectionTests):
    def _client(self):
        client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = HOST
        return client

    def test_workspace_and_guard_and_xml_target_v2(self):
        period, v1, _ = self._submit_v1()
        self._eu_service_invoice()
        self._hr_expense()
        client = self._client()

        draft_blocked = client.post('/api/tax/pdv/periods/2026-07/draft/')
        self.assertEqual(draft_blocked.status_code, 409)

        first = client.post('/api/tax/pdv/periods/2026-07/correction/')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()['return_version'], 2)

        second = client.post('/api/tax/pdv/periods/2026-07/correction/')
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()['detail'], CORRECTION_ALREADY_IN_PROGRESS)

        workspace = client.get('/api/tax/pdv/periods/2026-07/').json()
        self.assertEqual(workspace['period_status'], 'submitted')
        self.assertEqual(workspace['return_version'], v1.version)
        self.assertEqual(workspace['return_status'], 'submitted')
        self.assertEqual(workspace['latest_return_version'], 2)
        self.assertEqual(workspace['latest_return_status'], 'generated')
        self.assertTrue(workspace['correction_in_progress'])

        listing = client.get('/api/tax/pdv/periods/').json()['results']
        july = next(row for row in listing if row['period'] == '2026-07')
        self.assertTrue(july['correction_in_progress'])

        xml = client.get('/api/tax/pdv/periods/2026-07/xml/')
        self.assertEqual(xml.status_code, 200)
        self.assertIn(b'25000', xml.content)

        signed = _rewrite_signed_pdv_xml(
            oib='12345678901',
            period_from='2026-07-01',
            period_to='2026-07-31',
        )
        xml_uuid = _xml_document_uuid(signed)
        uploaded = SimpleUploadedFile('pdv.xml', signed, content_type='application/xml')
        submitted = client.post(
            '/api/tax/pdv/periods/2026-07/submit/',
            {'return_version': 2, 'submitted_xml': uploaded},
            format='multipart',
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertNotEqual(submitted.json()['external_identifier'], xml_uuid)

        v1.refresh_from_db()
        self.assertEqual(v1.status, VATReturnStatus.SUPERSEDED)
        period.refresh_from_db()
        self.assertEqual(period.status, 'submitted')
        self.assertEqual(period.latest_return.status, VATReturnStatus.SUBMITTED)
