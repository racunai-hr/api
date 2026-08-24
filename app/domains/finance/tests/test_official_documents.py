"""Acceptance tests for OfficialDocument (ADR-0028)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import JournalEntry, OfficialDocument
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

HOST = 'official.racunai.hr'
OTHER_HOST = 'official2.racunai.hr'
PDF_BYTES = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class OfficialDocumentWriteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='official', name='Official Co')
        cls.other = Tenant.objects.create(slug='official2', name='Other')
        User = get_user_model()
        cls.owner = User.objects.create_user(username='off-owner', password='test')
        cls.viewer = User.objects.create_user(username='off-viewer', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        cls.issuer = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Carinska uprava',
            tax_number='18683136487',
            partner_type='supplier',
            status='active',
            country_code='HR',
            country='Hrvatska',
        )
        cls.other_issuer = Partner.all_objects.create(
            tenant=cls.other,
            name='Other issuer',
            tax_number='12345678901',
            partner_type='supplier',
            status='active',
            country_code='HR',
            country='Hrvatska',
        )

    def setUp(self):
        self.client = self._client(self.owner)

    def _client(self, user, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def _pdf(self, name='rjesnje.pdf'):
        return SimpleUploadedFile(name, PDF_BYTES, content_type='application/pdf')

    def _payload(self, **overrides):
        body = {
            'official_kind': 'tax_decision',
            'issuer_id': self.issuer.pk,
            'document_number': 'UP/I-410-22/26-09/49557',
            'issue_date': '2026-08-03',
            'due_date': '2026-08-18',
            'amount': '10347.20',
            'currency': 'EUR',
            'reference': 'Audi A8',
            'notes': 'Vrijednosna 7051.54 / ekoloska 3295.66',
        }
        body.update(overrides)
        return body

    def test_create_without_file_stays_draft(self):
        response = self.client.post('/api/finance/official-documents/', self._payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'draft')
        self.assertFalse(response.data['has_file'])

    def test_register_without_pdf_is_400(self):
        created = self.client.post('/api/finance/official-documents/', self._payload(), format='json')
        doc_id = created.data['id']
        response = self.client.post(f'/api/finance/official-documents/{doc_id}/register/')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['code'], 'missing_pdf')
        self.assertEqual(
            OfficialDocument.all_objects.get(pk=doc_id).status,
            OfficialDocument.STATUS_DRAFT,
        )

    def test_register_with_pdf_uses_pk_in_path(self):
        created = self.client.post('/api/finance/official-documents/', self._payload(), format='json')
        doc_id = created.data['id']
        response = self.client.post(
            f'/api/finance/official-documents/{doc_id}/register/',
            {'file': self._pdf()},
            format='multipart',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'registered')
        document = OfficialDocument.all_objects.get(pk=doc_id)
        self.assertIn(f'/{doc_id}/', document.original_file.name)
        self.assertTrue(document.original_file.name.startswith(f'official_documents/{self.tenant.pk}/'))
        self.assertEqual(document.file_size, len(PDF_BYTES))
        self.assertEqual(len(document.file_sha256), 64)

    def test_create_and_register_in_one_request(self):
        body = self._payload()
        body['register'] = 'true'
        body['file'] = self._pdf()
        response = self.client.post('/api/finance/official-documents/', body, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'registered')
        document = OfficialDocument.all_objects.get(pk=response.data['id'])
        self.assertIn(f'/{document.pk}/', document.original_file.name)

    def test_invalid_kind_rejected(self):
        response = self.client.post(
            '/api/finance/official-documents/',
            self._payload(official_kind='customs_decision'),
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_issuer_number_conflicts(self):
        first = self.client.post('/api/finance/official-documents/', self._payload(), format='json')
        self.assertEqual(first.status_code, 201)
        second = self.client.post('/api/finance/official-documents/', self._payload(), format='json')
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data['code'], 'duplicate_document')

    def test_cancel(self):
        created = self.client.post('/api/finance/official-documents/', self._payload(), format='json')
        doc_id = created.data['id']
        response = self.client.post(f'/api/finance/official-documents/{doc_id}/cancel/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'cancelled')

    def test_link_journal_and_conflict(self):
        created = self.client.post('/api/finance/official-documents/', self._payload(), format='json')
        doc_id = created.data['id']
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202608-0099',
            entry_date=date(2026, 8, 3),
            description='PPMV obveza',
            status='posted',
            created_by=self.owner,
        )
        linked = self.client.post(
            f'/api/finance/official-documents/{doc_id}/link-journal/',
            {'journal_entry_id': entry.pk},
            format='json',
        )
        self.assertEqual(linked.status_code, 200, linked.data)
        entry.refresh_from_db()
        self.assertEqual(entry.source_object_id, doc_id)
        self.assertEqual(
            entry.source_content_type_id,
            ContentType.objects.get_for_model(OfficialDocument).pk,
        )
        other = self.client.post('/api/finance/official-documents/', self._payload(document_number='OTHER-1'), format='json')
        conflict = self.client.post(
            f'/api/finance/official-documents/{other.data["id"]}/link-journal/',
            {'journal_entry_id': entry.pk},
            format='json',
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['code'], 'source_taken')

    def test_viewer_cannot_create(self):
        response = self._client(self.viewer).post(
            '/api/finance/official-documents/',
            self._payload(),
            format='json',
        )
        self.assertEqual(response.status_code, 404)

    def test_other_tenant_is_404(self):
        created = self.client.post('/api/finance/official-documents/', self._payload(), format='json')
        doc_id = created.data['id']
        other_user = get_user_model().objects.create_user(username='off-other', password='test')
        TenantMembership.objects.create(user=other_user, tenant=self.other, role='owner')
        response = self._client(other_user, host=OTHER_HOST).get(
            f'/api/finance/official-documents/{doc_id}/'
        )
        self.assertEqual(response.status_code, 404)
