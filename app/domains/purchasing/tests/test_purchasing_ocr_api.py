"""Acceptance tests for Purchasing OCR invoice import API."""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import JournalEntry, SubledgerItem, VATLedgerEntry
from domains.purchasing.services.invoice_import import execute_invoice_import
from expenses.models import Expense, ExpenseCategory, IncomingInvoiceImport
from expenses.tests.partner_helpers import create_supplier_partner
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

HOST = 'ocr.racunai.hr'
OTHER_HOST = 'ocr2.racunai.hr'

# 1x1 PNG
PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489'
    '0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082'
)

FAKE_PAYLOAD = {
    'supplier': {
        'name': 'INA d.d.',
        'oib': '27759560625',
        'vat_number': '',
        'address': 'Avenija Veceslava Holjevca 10',
        'city': 'Zagreb',
        'postal_code': '10000',
        'country': 'Hrvatska',
        'iban': 'HR1210010051863000160',
    },
    'invoice_number': '123456/2026',
    'issue_date': '2026-08-18',
    'due_date': '2026-09-02',
    'currency': 'EUR',
    'net_amount': '100.00',
    'tax_amount': '25.00',
    'total_amount': '125.00',
    'iban': 'HR1210010051863000160',
    'vat_breakdown': [{'rate': '25.00', 'base': '100.00', 'amount': '25.00'}],
    'line_items': [
        {'description': 'Diesel Class Plus', 'quantity': '1', 'unit_price': '100.00', 'amount': '100.00'}
    ],
    'warnings': [],
}


def _png(name='racun.png'):
    return SimpleUploadedFile(name, PNG_BYTES, content_type='image/png')


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
    PURCHASING_OCR_PROVIDER='fake',
    PURCHASING_OCR_FAKE_BEHAVIOR='ok',
    PURCHASING_OCR_FAKE_PAYLOAD=FAKE_PAYLOAD,
    MEDIA_ROOT='/tmp/racunai_ocr_tests_media',
)
class PurchasingOcrApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='ocr', name='OCR Co')
        cls.other = Tenant.objects.create(slug='ocr2', name='Other OCR Co')
        User = get_user_model()
        cls.owner = User.objects.create_user(username='ocr-owner', password='test')
        cls.viewer = User.objects.create_user(username='ocr-viewer', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')

    def setUp(self):
        self.client = self._client(self.owner)

    def _client(self, user, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def _eager_enqueue(self, run_id: int):
        return execute_invoice_import(run_id)

    def _create_import(self, *, key='k1', file=None, client=None):
        client = client or self.client
        with patch(
            'domains.purchasing.services.invoice_import.enqueue_invoice_import',
            side_effect=self._eager_enqueue,
        ):
            return client.post(
                '/api/purchasing/invoices/import/',
                {'file': file or _png()},
                format='multipart',
                HTTP_IDEMPOTENCY_KEY=key,
            )

    def test_viewer_gets_404(self):
        response = self._client(self.viewer).post(
            '/api/purchasing/invoices/import/',
            {'file': _png()},
            format='multipart',
            HTTP_IDEMPOTENCY_KEY='v1',
        )
        self.assertEqual(response.status_code, 404)

    def test_idempotency_same_key_same_file(self):
        first = self._create_import(key='same')
        self.assertEqual(first.status_code, 202)
        second = self._create_import(key='same')
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertFalse(second.data['created'])

    def test_idempotency_same_key_different_file(self):
        self._create_import(key='reuse')
        other = SimpleUploadedFile(
            'other.png',
            PNG_BYTES + b'\x00',
            content_type='image/png',
        )
        response = self._create_import(key='reuse', file=other)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'idempotency_key_reused')

    def test_extract_and_match_existing_partner(self):
        partner = create_supplier_partner(
            tenant=self.tenant,
            name='INA Industrija nafte d.d.',
            tax_number='27759560625',
            address='',
        )
        response = self._create_import(key='match')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['status'], 'extracted')
        self.assertEqual(response.data['partner']['match'], 'exact_oib')
        self.assertEqual(response.data['partner']['partner_id'], partner.pk)
        self.assertEqual(response.data['extracted']['supplier']['country_code'], 'HR')
        self.assertTrue(any(row['field'] == 'address' for row in response.data['partner']['diff']))

    def test_create_partner_and_confirm_draft_without_finance_side_effects(self):
        response = self._create_import(key='confirm1')
        import_id = response.data['id']
        created = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': 'INA d.d.',
                'tax_number': '27759560625',
                'address': 'Avenija Veceslava Holjevca 10',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country': 'Hrvatska',
                'iban': 'HR1210010051863000160',
            },
            format='json',
        )
        self.assertEqual(created.status_code, 200)
        partner = Partner.all_objects.get(tenant=self.tenant, tax_number='27759560625')
        self.assertEqual(partner.country_code, 'HR')
        self.assertEqual(Partner.all_objects.filter(tenant=self.tenant, tax_number='27759560625').count(), 1)

        confirmed = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {},
            format='json',
        )
        self.assertEqual(confirmed.status_code, 200)
        expense = Expense.all_objects.get(pk=confirmed.data['confirmed_expense_id'])
        self.assertEqual(expense.status, 'draft')
        self.assertEqual(expense.source, 'ocr')
        self.assertEqual(expense.amount, Decimal('125.00'))
        ct = ContentType.objects.get_for_model(Expense)
        self.assertFalse(
            JournalEntry.all_objects.filter(
                source_content_type=ct,
                source_object_id=expense.pk,
            ).exists()
        )
        self.assertFalse(
            VATLedgerEntry.all_objects.filter(
                source_content_type=ct,
                source_object_id=expense.pk,
            ).exists()
        )
        self.assertFalse(
            SubledgerItem.all_objects.filter(
                source_content_type=ct,
                source_object_id=expense.pk,
            ).exists()
        )

        again = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {},
            format='json',
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data['confirmed_expense_id'], expense.pk)
        self.assertEqual(Expense.all_objects.filter(tenant=self.tenant, source='ocr').count(), 1)

    def test_create_partner_rejects_unknown_country_without_auto_hr(self):
        response = self._create_import(key='country-bad')
        import_id = response.data['id']
        missing = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': 'INA d.d.',
                'tax_number': '27759560625',
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
            },
            format='json',
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.data['code'], 'partner_country_invalid')
        unknown = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': 'INA d.d.',
                'tax_number': '27759560625',
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country': 'Narnia',
            },
            format='json',
        )
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.data['code'], 'partner_country_invalid')
        self.assertFalse(Partner.all_objects.filter(tenant=self.tenant, tax_number='27759560625').exists())

    def test_de_supplier_empty_tax_vat_create_and_rematch(self):
        de_payload = {
            'supplier': {
                'name': 'SaM Automobile',
                'oib': '',
                'vat_number': 'DE355497142',
                'address': 'Saegmuehlweg 4',
                'city': 'Sinsheim',
                'postal_code': '74889',
                'country': 'Deutschland',
                'iban': 'DE02672922000047505100',
            },
            'invoice_number': '2026-213',
            'issue_date': '2026-07-30',
            'due_date': None,
            'currency': 'EUR',
            'net_amount': '33000.00',
            'tax_amount': '0.00',
            'total_amount': '33000.00',
            'iban': 'DE02672922000047505100',
            'vat_breakdown': [{'rate': '0.00', 'base': '33000.00', 'amount': '0.00'}],
            'line_items': [
                {
                    'description': 'Audi A8 Lang 50 TDI',
                    'quantity': '1',
                    'unit_price': '33000.00',
                    'amount': '33000.00',
                }
            ],
            'warnings': [],
        }
        with self.settings(PURCHASING_OCR_FAKE_PAYLOAD=de_payload):
            first = self._create_import(key='de-sam-1')
        self.assertEqual(first.status_code, 202, getattr(first, 'data', first.content))
        self.assertEqual(first.data['status'], 'extracted')
        self.assertEqual(first.data['partner']['match'], 'missing')
        self.assertEqual(first.data['extracted']['supplier']['country_code'], 'DE')
        self.assertEqual(first.data['extracted']['supplier']['vat_number'], 'DE355497142')

        created = self.client.post(
            f"/api/purchasing/invoices/import/{first.data['id']}/create-partner/",
            {
                'name': 'SaM Automobile',
                'tax_number': '',
                'vat_number': 'DE355497142',
                'address': 'Saegmuehlweg 4',
                'city': 'Sinsheim',
                'postal_code': '74889',
                'country_code': 'DE',
                'iban': 'DE02672922000047505100',
            },
            format='json',
        )
        self.assertEqual(created.status_code, 200, getattr(created, 'data', created.content))
        partner = Partner.all_objects.get(tenant=self.tenant, vat_number='DE355497142')
        self.assertEqual(partner.tax_number, '')
        self.assertEqual(partner.country_code, 'DE')
        self.assertEqual(created.data['partner']['match'], 'vat')

        with self.settings(PURCHASING_OCR_FAKE_PAYLOAD=de_payload):
            second = self._create_import(key='de-sam-2')
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.data['partner']['match'], 'vat')
        self.assertEqual(second.data['partner']['partner_id'], partner.pk)
        self.assertEqual(
            Partner.all_objects.filter(tenant=self.tenant, vat_number='DE355497142').count(),
            1,
        )

        confirmed = self.client.post(
            f"/api/purchasing/invoices/import/{first.data['id']}/confirm/",
            {},
            format='json',
        )
        self.assertEqual(confirmed.status_code, 200)
        expense = Expense.all_objects.get(pk=confirmed.data['confirmed_expense_id'])
        self.assertEqual(expense.status, 'draft')
        self.assertEqual(expense.amount, Decimal('33000.00'))
        ct = ContentType.objects.get_for_model(Expense)
        self.assertFalse(
            JournalEntry.all_objects.filter(
                source_content_type=ct,
                source_object_id=expense.pk,
            ).exists()
        )

    def test_discard_while_processing_returns_409(self):
        run = IncomingInvoiceImport.all_objects.create(
            tenant=self.tenant,
            uploaded_by=self.owner,
            original_filename='x.png',
            content_type='image/png',
            file_sha256='a' * 64,
            file_size=1,
            status=IncomingInvoiceImport.STATUS_PROCESSING,
            idempotency_key='proc1',
        )
        run.original_file.save('x.png', SimpleUploadedFile('x.png', PNG_BYTES), save=True)
        response = self.client.post(f'/api/purchasing/invoices/import/{run.pk}/discard/')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'processing')

    def test_confirm_after_discard_rejected(self):
        response = self._create_import(key='discard1')
        import_id = response.data['id']
        self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': 'INA d.d.',
                'tax_number': '27759560625',
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country_code': 'HR',
            },
            format='json',
        )
        discarded = self.client.post(f'/api/purchasing/invoices/import/{import_id}/discard/')
        self.assertEqual(discarded.status_code, 200)
        confirmed = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {},
            format='json',
        )
        self.assertEqual(confirmed.status_code, 409)
        self.assertEqual(confirmed.data['code'], 'invalid_status')

    def test_partner_changed_on_apply(self):
        partner = create_supplier_partner(
            tenant=self.tenant,
            name='INA',
            tax_number='27759560625',
            address='',
            city='Zagreb',
            postal_code='10000',
        )
        response = self._create_import(key='race-partner')
        import_id = response.data['id']
        partner.address = 'Split'
        partner.save(update_fields=['address'])
        applied = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/apply-partner-updates/',
        )
        self.assertEqual(applied.status_code, 409)
        self.assertEqual(applied.data['code'], 'partner_changed')
        partner.refresh_from_db()
        self.assertEqual(partner.address, 'Split')

    def test_business_duplicate_requires_override(self):
        partner = create_supplier_partner(
            tenant=self.tenant,
            name='INA',
            tax_number='27759560625',
            address='A',
            city='Zagreb',
            postal_code='10000',
        )
        Expense.all_objects.create(
            tenant=self.tenant,
            expense_number='T-2026-0001',
            status='draft',
            category=ExpenseCategory.all_objects.get(tenant=self.tenant, name='Ostalo'),
            supplier=partner,
            amount=Decimal('125.00'),
            tax_amount=Decimal('25.00'),
            currency='EUR',
            expense_date=date(2026, 8, 18),
            receipt_number='123456/2026',
            description='Existing',
            created_by=self.owner,
        )
        response = self._create_import(key='biz-dup')
        import_id = response.data['id']
        self.assertEqual(response.data['duplicate']['kind'], 'business')
        blocked = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {},
            format='json',
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data['code'], 'duplicate_override_required')
        ok = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {'duplicate_override': True},
            format='json',
        )
        self.assertEqual(ok.status_code, 200)

    def test_hard_duplicate_blocks_confirm(self):
        first = self._create_import(key='hard1')
        import_id = first.data['id']
        self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': 'INA d.d.',
                'tax_number': '27759560625',
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country_code': 'HR',
            },
            format='json',
        )
        self.client.post(f'/api/purchasing/invoices/import/{import_id}/confirm/', {}, format='json')
        second = self._create_import(key='hard2')
        self.assertEqual(second.data['duplicate']['kind'], 'hard')
        blocked = self.client.post(
            f'/api/purchasing/invoices/import/{second.data["id"]}/confirm/',
            {'duplicate_override': True},
            format='json',
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data['code'], 'hard_duplicate')

    @override_settings(PURCHASING_OCR_FAKE_BEHAVIOR='timeout')
    def test_openai_timeout_fails_without_side_effects(self):
        response = self._create_import(key='timeout1')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['status'], 'failed')
        self.assertEqual(Expense.all_objects.filter(tenant=self.tenant).count(), 0)
        self.assertEqual(Partner.all_objects.filter(tenant=self.tenant).count(), 0)

    @override_settings(PURCHASING_OCR_FAKE_BEHAVIOR='invalid')
    def test_invalid_structured_output_fails(self):
        response = self._create_import(key='invalid1')
        self.assertEqual(response.data['status'], 'failed')

    def test_celery_retry_after_extracted_skips_provider(self):
        response = self._create_import(key='retry-extract')
        run_id = response.data['id']
        self.assertEqual(response.data['status'], 'extracted')
        with patch('domains.purchasing.services.invoice_import.get_extraction_provider') as provider:
            execute_invoice_import(run_id)
            provider.assert_not_called()


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
    PURCHASING_OCR_PROVIDER='fake',
    PURCHASING_OCR_FAKE_PAYLOAD=FAKE_PAYLOAD,
    MEDIA_ROOT='/tmp/racunai_ocr_tests_media_tx',
)
class PurchasingOcrParallelTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug='ocr', name='OCR Parallel')
        User = get_user_model()
        self.owner = User.objects.create_user(username='ocrp-owner', password='test')
        TenantMembership.objects.create(user=self.owner, tenant=self.tenant, role='owner')
        ExpenseCategory.all_objects.create(tenant=self.tenant, name='Ostalo')
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = HOST

    def _eager_enqueue(self, run_id: int):
        return execute_invoice_import(run_id)

    def test_parallel_confirm_creates_one_expense(self):
        with patch(
            'domains.purchasing.services.invoice_import.enqueue_invoice_import',
            side_effect=self._eager_enqueue,
        ):
            created = self.client.post(
                '/api/purchasing/invoices/import/',
                {'file': _png()},
                format='multipart',
                HTTP_IDEMPOTENCY_KEY='parallel-confirm',
            )
        self.assertEqual(created.status_code, 202, getattr(created, 'data', created.content))
        import_id = created.data['id']
        self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': 'INA d.d.',
                'tax_number': '27759560625',
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country_code': 'HR',
            },
            format='json',
        )
        results = []
        errors = []

        def worker():
            from django.db import connection

            connection.close()
            client = APIClient()
            token = RefreshToken.for_user(self.owner).access_token
            client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
            client.defaults['HTTP_HOST'] = HOST
            try:
                results.append(
                    client.post(
                        f'/api/purchasing/invoices/import/{import_id}/confirm/',
                        {},
                        format='json',
                    )
                )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(row.status_code == 200 for row in results))
        self.assertEqual(Expense.all_objects.filter(tenant=self.tenant, source='ocr').count(), 1)

    def test_parallel_create_partner_one_partner(self):
        with patch(
            'domains.purchasing.services.invoice_import.enqueue_invoice_import',
            side_effect=self._eager_enqueue,
        ):
            a = self.client.post(
                '/api/purchasing/invoices/import/',
                {'file': _png()},
                format='multipart',
                HTTP_IDEMPOTENCY_KEY='p1',
            )
            b = self.client.post(
                '/api/purchasing/invoices/import/',
                {'file': _png()},
                format='multipart',
                HTTP_IDEMPOTENCY_KEY='p2',
            )
        self.assertEqual(a.status_code, 202, getattr(a, 'data', a.content))
        self.assertEqual(b.status_code, 202, getattr(b, 'data', b.content))
        ids = [a.data['id'], b.data['id']]
        body = {
            'name': 'INA d.d.',
            'tax_number': '27759560625',
            'address': 'A',
            'city': 'Zagreb',
            'postal_code': '10000',
            'country_code': 'HR',
        }
        results = []

        def worker(import_id):
            from django.db import connection

            connection.close()
            client = APIClient()
            token = RefreshToken.for_user(self.owner).access_token
            client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
            client.defaults['HTTP_HOST'] = HOST
            results.append(
                client.post(
                    f'/api/purchasing/invoices/import/{import_id}/create-partner/',
                    body,
                    format='json',
                )
            )

        threads = [threading.Thread(target=worker, args=(import_id,)) for import_id in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(
            all(row.status_code == 200 for row in results),
            [getattr(row, 'data', row.content) for row in results],
        )
        self.assertEqual(Partner.all_objects.filter(tenant=self.tenant, tax_number='27759560625').count(), 1)
