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
from settings.models import CompanySettings
from tenants.models import Tenant, TenantMembership

HAC_PARTY = {
    'name': 'Hrvatske autoceste d.o.o.',
    'oib': '57500462912',
    'vat_number': '',
    'address': 'Šiška 1',
    'city': 'Zagreb',
    'postal_code': '10000',
    'country': 'HR',
    'iban': '',
}

FINE_STAR_PARTY = {
    'name': 'FINE STAR DOO',
    'oib': '36619131370',
    'vat_number': '',
    'address': 'BANA JOSIPA JELAČIĆA 58',
    'city': 'ŠIBENIK',
    'postal_code': '22000',
    'country': 'HR',
    'iban': '',
}


def _invoice_payload(*, issuer, buyer, **extra):
    payload = {
        'issuer': issuer,
        'buyer': buyer,
        'invoice_number': extra.get('invoice_number', '1432082-608-600'),
        'issue_date': extra.get('issue_date', '2026-09-18'),
        'due_date': extra.get('due_date', '2026-09-18'),
        'currency': 'EUR',
        'net_amount': extra.get('net_amount', '104.00'),
        'tax_amount': extra.get('tax_amount', '26.00'),
        'total_amount': extra.get('total_amount', '130.00'),
        'iban': extra.get('iban', ''),
        'vat_breakdown': [{'rate': '25.00', 'base': '104.00', 'amount': '26.00'}],
        'line_items': extra.get(
            'line_items',
            [{'description': 'Cestarina', 'quantity': '1', 'unit_price': '104.00', 'amount': '104.00'}],
        ),
        'warnings': [],
    }
    return payload


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
        self.assertEqual(expense.category.name, 'Ostalo')
        self.assertIsNone(expense.expense_account_id)
        lines = list(expense.lines.all())
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].description, 'Diesel Class Plus')
        self.assertEqual(lines[0].net_amount, Decimal('100.00'))
        self.assertEqual(lines[0].vat_amount, Decimal('25.00'))
        self.assertIsNone(lines[0].posting_account_id)
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

    def test_confirm_with_category_remembers_kind_not_account(self):
        response = self._create_import(key='confirm-cat')
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
        telekom = ExpenseCategory.all_objects.create(tenant=self.tenant, name='Telekomunikacije')
        confirmed = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {
                'category_id': telekom.pk,
                'remember_category_for_partner': True,
            },
            format='json',
        )
        self.assertEqual(confirmed.status_code, 200)
        expense = Expense.all_objects.get(pk=confirmed.data['confirmed_expense_id'])
        self.assertEqual(expense.category_id, telekom.pk)
        self.assertIsNone(expense.expense_account_id)
        partner = Partner.all_objects.get(tenant=self.tenant, tax_number='27759560625')
        self.assertEqual(partner.default_expense_category_id, telekom.pk)

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

    def _company(self, **kwargs):
        defaults = {
            'tenant': self.tenant,
            'company_name': 'Fine Star d.o.o.',
            'company_address': 'Ulica 1',
            'street': 'Ulica',
            'house_number': '1',
            'postal_code': '22000',
            'city': 'Šibenik',
            'country': 'HR',
            'company_phone': '091',
            'company_email': 'info@finestar.hr',
            'vat_number': '36619131370',
            'vat_id': 'HR36619131370',
        }
        defaults.update(kwargs)
        return CompanySettings.all_objects.create(**defaults)

    def test_retry_from_discarded_and_extracted(self):
        created = self._create_import(key='retry-discard')
        import_id = created.data['id']
        self.assertEqual(created.data['status'], 'extracted')
        discarded = self.client.post(f'/api/purchasing/invoices/import/{import_id}/discard/')
        self.assertEqual(discarded.status_code, 200)
        with patch(
            'domains.purchasing.services.invoice_import.enqueue_invoice_import',
            side_effect=self._eager_enqueue,
        ):
            retried = self.client.post(f'/api/purchasing/invoices/import/{import_id}/retry/')
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.data['status'], 'extracted')
        with patch(
            'domains.purchasing.services.invoice_import.enqueue_invoice_import',
            side_effect=self._eager_enqueue,
        ):
            again = self.client.post(f'/api/purchasing/invoices/import/{import_id}/retry/')
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data['status'], 'extracted')

    def test_hac_buyer_oib_is_case_4(self):
        self._company()
        payload = _invoice_payload(issuer=HAC_PARTY, buyer=FINE_STAR_PARTY)
        with self.settings(PURCHASING_OCR_FAKE_PAYLOAD=payload):
            response = self._create_import(key='hac-oib')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['direction']['code'], 'ok')
        self.assertEqual(response.data['extracted']['supplier']['name'], HAC_PARTY['name'])
        self.assertEqual(response.data['extracted']['supplier']['oib'], '57500462912')
        self.assertFalse(response.data['direction']['unresolved'])

    def test_hac_name_only_buyer_is_case_5(self):
        self._company()
        buyer = {**FINE_STAR_PARTY, 'oib': '', 'vat_number': ''}
        payload = _invoice_payload(issuer=HAC_PARTY, buyer=buyer)
        with self.settings(PURCHASING_OCR_FAKE_PAYLOAD=payload):
            response = self._create_import(key='hac-name')
        self.assertEqual(response.data['direction']['code'], 'tenant_not_on_document')
        self.assertTrue(response.data['direction']['override_required'])
        self.assertEqual(response.data['extracted']['supplier']['name'], HAC_PARTY['name'])
        buyer_candidate = next(
            row for row in response.data['direction']['party_candidates'] if row['role'] == 'buyer'
        )
        self.assertTrue(buyer_candidate['suspected_own_company'])
        self.assertFalse(buyer_candidate['is_own_company'])
        import_id = response.data['id']
        created = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': HAC_PARTY['name'],
                'tax_number': HAC_PARTY['oib'],
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country_code': 'HR',
            },
            format='json',
        )
        self.assertEqual(created.status_code, 200)
        blocked = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {},
            format='json',
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data['code'], 'direction_override_required')
        ok = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {'direction_override': True},
            format='json',
        )
        self.assertEqual(ok.status_code, 200)

    def test_swapped_issuer_oib_does_not_auto_swap(self):
        self._company()
        payload = _invoice_payload(issuer=FINE_STAR_PARTY, buyer=HAC_PARTY)
        with self.settings(PURCHASING_OCR_FAKE_PAYLOAD=payload):
            response = self._create_import(key='swapped-oib')
        self.assertEqual(response.data['direction']['code'], 'suspected_wrong_document_direction')
        self.assertTrue(response.data['direction']['unresolved'])
        self.assertEqual(response.data['extracted']['supplier']['name'], '')
        import_id = response.data['id']
        blocked = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {},
            format='json',
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data['code'], 'direction_unresolved')
        own = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/apply-supplier/',
            {'source': 'issuer'},
            format='json',
        )
        self.assertEqual(own.status_code, 400)
        self.assertEqual(own.data['code'], 'own_company_supplier')
        applied = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/apply-supplier/',
            {'source': 'buyer'},
            format='json',
        )
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied.data['extracted']['supplier']['name'], HAC_PARTY['name'])
        self.assertEqual(applied.data['direction']['supplier_source'], 'buyer')
        self.assertFalse(applied.data['direction']['unresolved'])

    def test_create_partner_rejects_own_oib_not_name(self):
        self._company()
        response = self._create_import(key='own-oib')
        import_id = response.data['id']
        own = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': 'Netko drugi',
                'tax_number': '36619131370',
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country_code': 'HR',
            },
            format='json',
        )
        self.assertEqual(own.status_code, 400)
        self.assertEqual(own.data['code'], 'own_company_supplier')
        named = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': 'FINE STAR DOO',
                'tax_number': '27759560625',
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country_code': 'HR',
            },
            format='json',
        )
        self.assertEqual(named.status_code, 200)

    def test_confirm_rejects_matched_own_company_partner(self):
        self._company(vat_number='27759560625', vat_id='HR27759560625')
        partner = create_supplier_partner(
            tenant=self.tenant,
            name='INA',
            tax_number='27759560625',
            address='A',
            city='Zagreb',
            postal_code='10000',
        )
        payload = _invoice_payload(
            issuer={
                'name': 'INA d.d.',
                'oib': '11111111111',
                'vat_number': '',
                'address': 'A',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country': 'HR',
                'iban': '',
            },
            buyer=FINE_STAR_PARTY,
        )
        with self.settings(PURCHASING_OCR_FAKE_PAYLOAD=payload):
            response = self._create_import(key='confirm-own')
        import_id = response.data['id']
        run = IncomingInvoiceImport.all_objects.get(pk=import_id)
        run.matched_partner = partner
        run.partner_match = IncomingInvoiceImport.MATCH_EXACT_OIB
        run.save(update_fields=['matched_partner', 'partner_match'])
        blocked = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {'direction_override': True},
            format='json',
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data['code'], 'own_company_supplier')

    def _postable_accounts(self):
        from accounting.models import AccountType, ChartOfAccounts

        account_type, _ = AccountType.all_objects.get_or_create(
            tenant=self.tenant,
            name='asset',
            defaults={'description': ''},
        )
        prepaid, _ = ChartOfAccounts.all_objects.get_or_create(
            tenant=self.tenant,
            account_code='1900',
            defaults={
                'account_name': 'Unaprijed plaćeni troškovi održavanja',
                'account_type': account_type,
                'account_class': '1',
                'is_postable': True,
                'is_active': True,
            },
        )
        inventory, _ = ChartOfAccounts.all_objects.get_or_create(
            tenant=self.tenant,
            account_code='4040',
            defaults={
                'account_name': 'Sitni inventar',
                'account_type': account_type,
                'account_class': '4',
                'is_postable': True,
                'is_active': True,
            },
        )
        return prepaid, inventory

    def _hac_enc_payload(self):
        return _invoice_payload(
            issuer=HAC_PARTY,
            buyer=FINE_STAR_PARTY,
            invoice_number='1432082-608-600',
            line_items=[
                {
                    'amount': '100.00',
                    'quantity': '1',
                    'unit_price': '127.78',
                    'description': 'Uplata iznosa - ENC za kat. I',
                },
                {
                    'amount': '15.00',
                    'quantity': '1',
                    'unit_price': '15.00',
                    'description': 'UREDAJ ENC - KOMPLET Prepaid, za kat. I',
                },
                {
                    'amount': '15.00',
                    'quantity': '1',
                    'unit_price': '15.00',
                    'description': 'UREDAJ ENC - KOMPLET Prepaid, za kat. I',
                },
            ],
        )

    def test_extract_exposes_allocated_ocr_lines(self):
        payload = self._hac_enc_payload()
        with self.settings(PURCHASING_OCR_FAKE_PAYLOAD=payload):
            response = self._create_import(key='alloc-lines')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data['extracted']['allocated_lines'],
            [
                {
                    'position': 1,
                    'description': 'Uplata iznosa - ENC za kat. I',
                    'net_amount': '80.00',
                    'vat_amount': '20.00',
                    'gross_amount': '100.00',
                },
                {
                    'position': 2,
                    'description': 'UREDAJ ENC - KOMPLET Prepaid, za kat. I',
                    'net_amount': '12.00',
                    'vat_amount': '3.00',
                    'gross_amount': '15.00',
                },
                {
                    'position': 3,
                    'description': 'UREDAJ ENC - KOMPLET Prepaid, za kat. I',
                    'net_amount': '12.00',
                    'vat_amount': '3.00',
                    'gross_amount': '15.00',
                },
            ],
        )

    def _confirm_hac_enc(self, *, key, line_accounts, extra=None):
        create_supplier_partner(
            tenant=self.tenant,
            name=HAC_PARTY['name'],
            tax_number=HAC_PARTY['oib'],
            address=HAC_PARTY['address'],
            city=HAC_PARTY['city'],
            postal_code=HAC_PARTY['postal_code'],
        )
        payload = self._hac_enc_payload()
        with self.settings(PURCHASING_OCR_FAKE_PAYLOAD=payload):
            response = self._create_import(key=key)
        body = {'line_accounts': line_accounts, **(extra or {})}
        return self.client.post(
            f"/api/purchasing/invoices/import/{response.data['id']}/confirm/",
            body,
            format='json',
        )

    def test_confirm_persists_three_lines_with_posting_accounts(self):
        prepaid, inventory = self._postable_accounts()
        confirmed = self._confirm_hac_enc(
            key='hac-split-confirm',
            line_accounts=[
                {'position': 1, 'posting_account_id': prepaid.pk},
                {'position': 2, 'posting_account_id': inventory.pk},
                {'position': 3, 'posting_account_id': inventory.pk},
            ],
        )
        self.assertEqual(confirmed.status_code, 200, getattr(confirmed, 'data', confirmed.content))
        expense = Expense.all_objects.get(pk=confirmed.data['confirmed_expense_id'])
        lines = list(expense.lines.all())
        self.assertEqual(len(lines), 3)
        self.assertEqual(
            [(row.position, row.net_amount, row.vat_amount, row.posting_account.account_code) for row in lines],
            [
                (1, Decimal('80.00'), Decimal('20.00'), '1900'),
                (2, Decimal('12.00'), Decimal('3.00'), '4040'),
                (3, Decimal('12.00'), Decimal('3.00'), '4040'),
            ],
        )

    def test_confirm_mixed_line_accounts_is_400(self):
        prepaid, _inventory = self._postable_accounts()
        blocked = self._confirm_hac_enc(
            key='hac-mixed-confirm',
            line_accounts=[{'position': 1, 'posting_account_id': prepaid.pk}],
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.data['code'], 'mixed_line_accounts')
        self.assertFalse(Expense.all_objects.filter(tenant=self.tenant, source='ocr').exists())

    def test_confirm_all_empty_line_accounts_is_header_only(self):
        confirmed = self._confirm_hac_enc(key='hac-header-confirm', line_accounts=[])
        self.assertEqual(confirmed.status_code, 200, getattr(confirmed, 'data', confirmed.content))
        expense = Expense.all_objects.get(pk=confirmed.data['confirmed_expense_id'])
        lines = list(expense.lines.all())
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(row.posting_account_id is None for row in lines))

    def test_confirm_line_allocation_mismatch_is_400(self):
        with patch(
            'domains.purchasing.services.confirm.allocated_rows_match_header',
            return_value=False,
        ):
            blocked = self._confirm_hac_enc(key='hac-mismatch-confirm', line_accounts=[])
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.data['code'], 'line_allocation_mismatch')


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
