"""FineStar category seed + OCR confirm → preview → approve → JournalEntry."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import ChartOfAccounts, JournalEntry, JournalEntryLine
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules
from accounting.services.rrif_import import import_rrif_chart
from domains.purchasing.services.invoice_import import execute_invoice_import
from expenses.models import Expense, ExpenseCategory
from partners.models import Partner
from tenants.management.commands.provision_finestar import (
    FINE_STAR_EXPENSE_CATEGORIES,
    apply_finestar_expense_categories,
)
from tenants.models import Tenant, TenantMembership

HOST = 'fsflow.racunai.hr'

PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489'
    '0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082'
)

TELECOM_PAYLOAD = {
    'supplier': {
        'name': 'A1 Hrvatska d.o.o.',
        'oib': '27759560625',
        'vat_number': '',
        'address': 'Vrtni put 1',
        'city': 'Zagreb',
        'postal_code': '10000',
        'country': 'Hrvatska',
        'iban': 'HR1210010051863000160',
    },
    'invoice_number': 'FS-SEED-4100',
    'issue_date': '2026-08-18',
    'due_date': '2026-09-02',
    'currency': 'EUR',
    'net_amount': '100.00',
    'tax_amount': '25.00',
    'total_amount': '125.00',
    'iban': 'HR1210010051863000160',
    'vat_breakdown': [{'rate': '25.00', 'base': '100.00', 'amount': '25.00'}],
    'line_items': [
        {'description': 'Poslovni internet', 'quantity': '1', 'unit_price': '100.00', 'amount': '100.00'}
    ],
    'warnings': [],
}


@override_settings(
    ALLOWED_HOSTS=[HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
    PURCHASING_OCR_PROVIDER='fake',
    PURCHASING_OCR_FAKE_BEHAVIOR='ok',
    PURCHASING_OCR_FAKE_PAYLOAD=TELECOM_PAYLOAD,
    MEDIA_ROOT='/tmp/racunai_fsflow_ocr_media',
)
class FineStarExpensePostingFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='fsflow', name='FineStar flow')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        applied = dict(apply_finestar_expense_categories(cls.tenant))
        cls.expected = dict(FINE_STAR_EXPENSE_CATEGORIES)
        assert applied == cls.expected
        User = get_user_model()
        cls.owner = User.objects.create_user(username='fsflow-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = HOST

    def _create_import(self):
        with patch(
            'domains.purchasing.services.invoice_import.enqueue_invoice_import',
            side_effect=lambda run_id: execute_invoice_import(run_id),
        ):
            return self.client.post(
                '/api/purchasing/invoices/import/',
                {'file': SimpleUploadedFile('racun.png', PNG_BYTES, content_type='image/png')},
                format='multipart',
                HTTP_IDEMPOTENCY_KEY='fsflow-ocr-1',
            )

    def test_seeded_categories_use_distinct_postable_rrif_accounts(self):
        mapped = {
            cat.name: cat.default_account.account_code
            for cat in ExpenseCategory.all_objects.filter(
                tenant=self.tenant,
                name__in=self.expected,
            )
        }
        self.assertEqual(mapped, self.expected)
        self.assertNotEqual(set(mapped.values()), {'4120'})
        for code in mapped.values():
            account = ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)
            self.assertTrue(account.is_postable)
            self.assertTrue(account.is_active)

    def test_ocr_category_preview_approve_journal_matches_plan(self):
        created = self._create_import()
        self.assertEqual(created.status_code, 202)
        import_id = created.data['id']
        partner_resp = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/create-partner/',
            {
                'name': 'A1 Hrvatska d.o.o.',
                'tax_number': '27759560625',
                'address': 'Vrtni put 1',
                'city': 'Zagreb',
                'postal_code': '10000',
                'country': 'Hrvatska',
                'iban': 'HR1210010051863000160',
            },
            format='json',
        )
        self.assertEqual(partner_resp.status_code, 200)
        telekom = ExpenseCategory.all_objects.get(tenant=self.tenant, name='Telekomunikacije')
        confirmed = self.client.post(
            f'/api/purchasing/invoices/import/{import_id}/confirm/',
            {'category_id': telekom.pk},
            format='json',
        )
        self.assertEqual(confirmed.status_code, 200)
        expense_id = confirmed.data['confirmed_expense_id']
        expense = Expense.all_objects.get(pk=expense_id)
        self.assertEqual(expense.status, 'draft')
        self.assertEqual(expense.source, 'ocr')
        self.assertEqual(expense.category_id, telekom.pk)
        self.assertIsNone(expense.expense_account_id)

        preview = self.client.get(f'/api/finance/expenses/{expense_id}/posting-preview/')
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data['can_approve'])
        self.assertEqual(preview.data['account_source'], 'category_default')
        net = [line for line in preview.data['lines'] if line['amount_field'] == 'net_amount']
        tax = [line for line in preview.data['lines'] if line['amount_field'] == 'tax_amount']
        self.assertEqual(net[0]['debit']['code'], '4100')
        self.assertEqual(tax[0]['debit']['code'], '1400')
        self.assertEqual(net[0]['credit']['code'], tax[0]['credit']['code'])

        approved = self.client.post(f'/api/finance/expenses/{expense_id}/approve/')
        self.assertEqual(approved.status_code, 200)
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'approved')

        ct = ContentType.objects.get_for_model(Expense)
        entry = JournalEntry.all_objects.get(
            tenant=self.tenant,
            source_content_type=ct,
            source_object_id=expense.pk,
            description__startswith='[expense_approved]',
            status='posted',
        )
        posted = list(
            JournalEntryLine.objects.filter(journal_entry=entry).order_by('id').values_list(
                'account__account_code', 'debit_amount', 'credit_amount',
            )
        )
        planned = []
        for line in preview.data['lines']:
            planned.append((line['debit']['code'], Decimal(line['amount']), Decimal('0')))
            planned.append((line['credit']['code'], Decimal('0'), Decimal(line['amount'])))
        self.assertEqual(posted, planned)
        self.assertIn(('4100', Decimal('100.00'), Decimal('0')), posted)
        self.assertIn(('1400', Decimal('25.00'), Decimal('0')), posted)

        locked = self.client.patch(
            f'/api/finance/expenses/{expense_id}/',
            {'category_id': telekom.pk},
            format='json',
        )
        self.assertEqual(locked.status_code, 409)
        self.assertEqual(locked.data['code'], 'not_draft')
        self.assertTrue(Partner.all_objects.filter(pk=expense.supplier_id).exists())
