"""Acceptance tests for PDV / PDV-S period workflow API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import PDVSReturn, VATLedgerEntry, VATPeriod, VATReturn, VATReturnStatus
from settings.models import CompanySettings, ResponsiblePerson, TaxOffice
from tenants.models import Tenant, TenantMembership

HOST = 'pdvwf.racunai.hr'
OTHER_HOST = 'pdvwfother.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class PdvWorkflowApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='pdvwf', name='PDV Workflow Co')
        cls.other = Tenant.objects.create(slug='pdvwfother', name='Other Workflow Co')
        User = get_user_model()
        cls.viewer = User.objects.create_user(username='pdvwf-viewer', password='test')
        cls.accountant = User.objects.create_user(username='pdvwf-accountant', password='test')
        cls.outsider = User.objects.create_user(username='pdvwf-outsider', password='test')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        TenantMembership.objects.create(user=cls.accountant, tenant=cls.tenant, role='accountant')
        TenantMembership.objects.create(user=cls.outsider, tenant=cls.other, role='viewer')

        tax_office, _ = TaxOffice.objects.get_or_create(
            code='3566',
            defaults={'name': 'Porezna uprava', 'city': 'Šibenik'},
        )
        settings = CompanySettings.all_objects.create(
            tenant=cls.tenant,
            company_name='Workflow d.o.o.',
            company_address='Ulica 1\n10000 Zagreb',
            street='Ulica',
            house_number='1',
            postal_code='10000',
            city='Zagreb',
            company_phone='+385 1 000 000',
            company_email='info@workflow.example',
            vat_number='12345678901',
            tax_office=tax_office,
        )
        ResponsiblePerson.objects.create(
            company_settings=settings,
            title='accountant',
            first_name='Ana',
            last_name='Računovođa',
        )

        cls.jul = VATPeriod.all_objects.create(
            tenant=cls.tenant,
            year=2026,
            month=7,
            status='submitted',
            submitted_at=timezone.now(),
        )
        cls.aug = VATPeriod.all_objects.create(tenant=cls.tenant, year=2026, month=8, status='open')
        VATPeriod.all_objects.create(tenant=cls.other, year=2026, month=7, status='open')

        VATLedgerEntry.all_objects.create(
            tenant=cls.tenant,
            vat_period=cls.jul,
            ledger_type=VATLedgerEntry.LEDGER_I_RA,
            entry_date=date(2026, 7, 10),
            document_number='OUT-JUL',
            partner_name='Kupac',
            partner_oib='12345678901',
            base_amount=Decimal('100.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('25.00'),
            vat_box='203',
        )
        VATLedgerEntry.all_objects.create(
            tenant=cls.tenant,
            vat_period=cls.aug,
            ledger_type=VATLedgerEntry.LEDGER_U_RA,
            entry_date=date(2026, 8, 5),
            document_number='EU-AUG',
            partner_name='EU Supplier',
            partner_oib='DE123456789',
            base_amount=Decimal('80.00'),
            vat_rate=Decimal('0.00'),
            vat_amount=Decimal('0.00'),
            vat_box='207',
        )

    def _auth_client(self, user=None, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user or self.accountant).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def test_anonymous_period_routes_401(self):
        client = APIClient()
        client.defaults['HTTP_HOST'] = HOST
        for path in (
            '/api/tax/pdv/periods/2026-07/',
            '/api/tax/pdv/periods/2026-07/xml/',
            '/api/tax/pdv-s/periods/2026-07/',
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertIn('Bearer', response.get('WWW-Authenticate', ''))

    def test_outsider_and_invalid_period_404(self):
        outsider = self._auth_client(user=self.outsider, host=HOST)
        self.assertEqual(outsider.get('/api/tax/pdv/periods/2026-07/').status_code, 404)
        client = self._auth_client()
        for path in (
            '/api/tax/pdv/periods/2026-13/',
            '/api/tax/pdv/periods/2026-8/',
            '/api/tax/pdv/periods/2026-09/',
            '/api/tax/pdv-s/periods/2026-09/',
        ):
            self.assertEqual(client.get(path).status_code, 404, path)
        self.assertEqual(client.post('/api/tax/pdv/periods/2026-09/draft/').status_code, 404)
        self.assertEqual(
            client.post('/api/tax/pdv/periods/2026-09/submit/', {}, format='json').status_code,
            404,
        )

    def test_viewer_cannot_write(self):
        client = self._auth_client(user=self.viewer)
        self.assertEqual(client.post('/api/tax/pdv/periods/2026-08/ledger/').status_code, 404)
        self.assertEqual(client.post('/api/tax/pdv/periods/2026-08/draft/').status_code, 404)

    def test_two_periods_do_not_leak(self):
        client = self._auth_client()
        july = client.get('/api/tax/pdv/periods/2026-07/').json()
        august = client.get('/api/tax/pdv/periods/2026-08/').json()
        self.assertEqual(july['period'], '2026-07')
        self.assertEqual(august['period'], '2026-08')
        self.assertEqual(july['period_status'], 'submitted')
        self.assertEqual(august['period_status'], 'open')
        self.assertEqual(july['vat_due'], '25.00')
        self.assertEqual(august['vat_due'], '0.00')
        self.assertNotIn('id', july)
        self.assertIn('xml_integrity', july)
        self.assertIn('event_uuid', july)

        july_s = client.get('/api/tax/pdv-s/periods/2026-07/').json()
        august_s = client.get('/api/tax/pdv-s/periods/2026-08/').json()
        self.assertEqual(july_s['rows'], [])
        self.assertEqual(july_s['row_count'], 0)
        self.assertEqual(august_s['row_count'], 1)
        self.assertEqual(august_s['rows'][0]['country_code'], 'DE')
        self.assertEqual(august_s['total_goods'], '80.00')

    def test_existing_period_is_reused_missing_period_only_ledger_creates(self):
        client = self._auth_client()
        self.assertEqual(VATPeriod.all_objects.filter(tenant=self.tenant, year=2026, month=7).count(), 1)
        client.get('/api/tax/pdv/periods/2026-07/')
        self.assertEqual(VATPeriod.all_objects.filter(tenant=self.tenant, year=2026, month=7).count(), 1)

        created = client.post('/api/tax/pdv/periods/2026-09/ledger/')
        self.assertEqual(created.status_code, 200)
        self.assertEqual(
            VATPeriod.all_objects.filter(tenant=self.tenant, year=2026, month=9, status='open').count(),
            1,
        )
        self.assertEqual(client.get('/api/tax/pdv/periods/2026-09/').status_code, 200)

        submitted = client.post('/api/tax/pdv/periods/2026-07/ledger/')
        self.assertEqual(submitted.status_code, 409)

    def test_pdv_draft_xml_submit_confirmation_and_version_mismatch(self):
        client = self._auth_client()
        draft = client.post('/api/tax/pdv/periods/2026-08/draft/')
        self.assertEqual(draft.status_code, 201)
        body = draft.json()
        self.assertEqual(body['period'], '2026-08')
        self.assertEqual(body['return_version'], 1)
        self.assertEqual(body['xml_integrity'], 'SYNC')
        self.assertNotIn('id', body)

        xml = client.get('/api/tax/pdv/periods/2026-08/xml/')
        self.assertEqual(xml.status_code, 200)
        self.assertIn('xml', xml['Content-Type'])
        self.assertTrue(xml.content.startswith(b'<?xml') or b'PDV' in xml.content)

        viewer_xml = self._auth_client(user=self.viewer).get('/api/tax/pdv/periods/2026-08/xml/')
        self.assertEqual(viewer_xml.status_code, 200)

        mismatch = client.post(
            '/api/tax/pdv/periods/2026-08/submit/',
            {
                'eporezna_identifier': str(uuid4()),
                'submitted_at': '2026-09-10T12:00:00+02:00',
                'return_version': 99,
            },
            format='json',
        )
        self.assertEqual(mismatch.status_code, 409)

        submitted = client.post(
            '/api/tax/pdv/periods/2026-08/submit/',
            {
                'eporezna_identifier': str(uuid4()),
                'submitted_at': '2026-09-10T12:00:00+02:00',
                'return_version': 1,
            },
            format='json',
        )
        self.assertEqual(submitted.status_code, 200)
        data = submitted.json()
        self.assertFalse(data['has_confirmation'])
        self.assertIn('event_uuid', data)

        pdf = SimpleUploadedFile('potvrda.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        attached = client.post(
            f'/api/tax/submissions/{data["event_uuid"]}/confirmation/',
            {'confirmation': pdf},
            format='multipart',
        )
        self.assertEqual(attached.status_code, 200)
        self.assertTrue(attached.json()['has_confirmation'])

        again = client.post(
            f'/api/tax/submissions/{data["event_uuid"]}/confirmation/',
            {
                'confirmation': SimpleUploadedFile(
                    'other.pdf',
                    b'%PDF-1.4 x',
                    content_type='application/pdf',
                ),
            },
            format='multipart',
        )
        self.assertEqual(again.status_code, 409)

    def test_pdv_xml_out_of_sync_409(self):
        VATReturn.all_objects.create(
            tenant=self.tenant,
            vat_period=self.aug,
            version=1,
            status=VATReturnStatus.GENERATED,
            payload_snapshot={'fields': {}},
            payload_hash='a' * 64,
            payload_json=ContentFile(b'{}', name='aug.json'),
        )
        response = self._auth_client().get('/api/tax/pdv/periods/2026-08/xml/')
        self.assertEqual(response.status_code, 409)

    def test_pdv_s_xml_and_submit_without_attachment(self):
        client = self._auth_client()
        xml = client.get('/api/tax/pdv-s/periods/2026-08/xml/')
        self.assertEqual(xml.status_code, 200)
        self.assertIn('xml', xml['Content-Type'])
        self.assertFalse(PDVSReturn.all_objects.filter(vat_period=self.aug).exists())

        submitted = client.post(
            '/api/tax/pdv-s/periods/2026-08/submit/',
            {
                'eporezna_identifier': str(uuid4()),
                'submitted_at': '2026-09-12T08:00:00+02:00',
            },
            format='json',
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertFalse(submitted.json()['has_confirmation'])
        self.assertTrue(PDVSReturn.all_objects.filter(vat_period=self.aug).exists())
        self.aug.refresh_from_db()
        self.assertEqual(self.aug.status, 'open')
        workspace = client.get('/api/tax/pdv/periods/2026-08/').json()
        self.assertEqual(workspace['period_status'], 'open')
        self.assertIsNone(workspace['return_status'])
        pdv_s = client.get('/api/tax/pdv-s/periods/2026-08/').json()
        self.assertEqual(pdv_s['event_uuid'], submitted.json()['event_uuid'])
        self.assertEqual(pdv_s['current_submission']['event_uuid'], submitted.json()['event_uuid'])
        self.assertEqual(pdv_s['current_submission']['submission_no'], 1)
        self.assertEqual(pdv_s['current_submission']['submission_type'], 'initial')
        self.assertEqual(len(pdv_s['submissions']), 1)
        self.assertFalse(pdv_s['current_submission']['has_confirmation'])
        self.assertEqual(len(pdv_s['current_submission']['payload_hash']), 64)
        ledger = client.post('/api/tax/pdv/periods/2026-08/ledger/')
        self.assertEqual(ledger.status_code, 200)
        draft = client.post('/api/tax/pdv/periods/2026-08/draft/')
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(draft.json()['return_status'], VATReturnStatus.GENERATED)
        self.aug.refresh_from_db()
        self.assertEqual(self.aug.status, 'open')

    def test_pdv_s_correction_history_keeps_hashes_and_confirmations(self):
        client = self._auth_client()
        first = client.post(
            '/api/tax/pdv-s/periods/2026-08/submit/',
            {
                'eporezna_identifier': str(uuid4()),
                'submitted_at': '2026-09-12T08:00:00+02:00',
            },
            format='json',
        )
        self.assertEqual(first.status_code, 200)
        first_uuid = first.json()['event_uuid']
        attached = client.post(
            f'/api/tax/submissions/{first_uuid}/confirmation/',
            {
                'confirmation': SimpleUploadedFile(
                    'pdvs-1.pdf',
                    b'%PDF-1.4 first',
                    content_type='application/pdf',
                ),
            },
            format='multipart',
        )
        self.assertEqual(attached.status_code, 200)

        VATLedgerEntry.all_objects.create(
            tenant=self.tenant,
            vat_period=self.aug,
            ledger_type=VATLedgerEntry.LEDGER_U_RA,
            entry_date=date(2026, 8, 20),
            document_number='EU-AUG-2',
            partner_name='EU Supplier B',
            partner_oib='DE355497142',
            base_amount=Decimal('10000.00'),
            vat_rate=Decimal('0.00'),
            vat_amount=Decimal('0.00'),
            vat_box='207',
        )

        second = client.post(
            '/api/tax/pdv-s/periods/2026-08/submit/',
            {
                'eporezna_identifier': str(uuid4()),
                'submitted_at': '2026-09-20T10:00:00+02:00',
            },
            format='json',
        )
        self.assertEqual(second.status_code, 200)
        second_uuid = second.json()['event_uuid']
        self.assertNotEqual(first_uuid, second_uuid)

        pdv_s = client.get('/api/tax/pdv-s/periods/2026-08/').json()
        self.assertEqual(pdv_s['event_uuid'], second_uuid)
        self.assertEqual(pdv_s['current_submission']['event_uuid'], second_uuid)
        self.assertEqual(pdv_s['current_submission']['submission_no'], 2)
        self.assertEqual(pdv_s['current_submission']['submission_type'], 'correction')
        self.assertFalse(pdv_s['current_submission']['has_confirmation'])
        self.assertEqual(len(pdv_s['submissions']), 2)
        initial, correction = pdv_s['submissions']
        self.assertEqual(initial['event_uuid'], first_uuid)
        self.assertEqual(initial['submission_no'], 1)
        self.assertEqual(initial['submission_type'], 'initial')
        self.assertTrue(initial['has_confirmation'])
        self.assertEqual(correction['event_uuid'], second_uuid)
        self.assertNotEqual(initial['payload_hash'], correction['payload_hash'])
        self.assertEqual(len(initial['payload_hash']), 64)
        self.assertEqual(len(correction['payload_hash']), 64)
        self.aug.refresh_from_db()
        self.assertEqual(self.aug.status, 'open')
        self.assertEqual(PDVSReturn.all_objects.filter(vat_period=self.aug).count(), 1)

    def test_boxes_are_period_scoped(self):
        client = self._auth_client()
        july = client.get('/api/tax/pdv/periods/2026-07/boxes/').json()
        august = client.get('/api/tax/pdv/periods/2026-08/boxes/').json()
        self.assertEqual(july['period'], '2026-07')
        self.assertEqual(august['period'], '2026-08')
        self.assertEqual(july['vat_due'], '25.00')
        self.assertEqual(august['vat_due'], '0.00')
        self.assertNotIn('taxpayer', july)
        self.assertIn('fields', july)
