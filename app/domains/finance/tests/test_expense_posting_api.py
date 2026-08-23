"""Finance/purchasing APIs for expense posting inputs and preview."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import ChartOfAccounts, JournalEntryLine
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import build_document_posting_plan, ensure_default_posting_rules, post_document
from accounting.services.rrif_import import import_rrif_chart
from domains.finance.services.expenses import posting_preview, serialize_document_posting_plan
from expenses.models import Expense, ExpenseAccountSource, ExpenseCategory, ExpenseSource
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

HOST = 'exppost.racunai.hr'
OTHER_HOST = 'exppost2.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class ExpensePostingApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='exppost', name='Exp Post')
        cls.other = Tenant.objects.create(slug='exppost2', name='Other')
        provision_tenant_chart(cls.tenant)
        provision_tenant_chart(cls.other)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.owner = User.objects.create_user(username='exppost-owner', password='test')
        cls.viewer = User.objects.create_user(username='exppost-viewer', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        TenantMembership.objects.create(user=cls.viewer, tenant=cls.tenant, role='viewer')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='A1',
            partner_type='supplier',
            status='active',
            address='U',
            city='Zagreb',
            postal_code='10000',
        )
        cls.ostalo = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.telekom = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Telekomunikacije',
            default_account=ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4100'),
        )
        cls.account_4100 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4100')
        cls.account_4123 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4123')

    def setUp(self):
        self.client = self._client(self.owner)

    def _client(self, user, host=HOST):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        client.defaults['HTTP_HOST'] = host
        return client

    def _expense(self, **overrides) -> Expense:
        defaults = {
            'tenant': self.tenant,
            'expense_number': f'T-API-{Expense.all_objects.filter(tenant=self.tenant).count() + 1}',
            'source': ExpenseSource.MANUAL,
            'status': 'draft',
            'category': self.ostalo,
            'supplier': self.supplier,
            'amount': Decimal('125.00'),
            'tax_amount': Decimal('25.00'),
            'currency': 'EUR',
            'expense_date': date(2026, 8, 1),
            'description': 'API posting',
            'created_by': self.owner,
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def test_chart_of_accounts_lists_postable_only(self):
        response = self.client.get('/api/finance/chart-of-accounts/?search=4100')
        self.assertEqual(response.status_code, 200)
        codes = {row['code'] for row in response.data['results']}
        self.assertIn('4100', codes)
        synthetic = ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code='41')
        self.assertFalse(synthetic.is_postable)
        self.assertNotIn('41', codes)
        other_ids = set(
            ChartOfAccounts.all_objects.filter(tenant=self.other, is_postable=True).values_list('pk', flat=True)
        )
        listed_ids = {row['id'] for row in response.data['results']}
        self.assertFalse(listed_ids & other_ids)

    def test_expense_categories_list_and_patch(self):
        listed = self.client.get('/api/purchasing/expense-categories/')
        self.assertEqual(listed.status_code, 200)
        names = {row['name'] for row in listed.data['results']}
        self.assertEqual(names, {'Ostalo', 'Telekomunikacije'})
        patched = self.client.patch(
            f'/api/purchasing/expense-categories/{self.ostalo.pk}/',
            {'default_account_id': self.account_4123.pk},
            format='json',
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.data['default_account']['code'], '4123')
        self.ostalo.refresh_from_db()
        self.assertEqual(self.ostalo.default_account_id, self.account_4123.pk)

    def test_preview_serializes_builder_plan_only(self):
        expense = self._expense(category=self.telekom)
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        expected = serialize_document_posting_plan(expense, plan)
        response = self.client.get(f'/api/finance/expenses/{expense.pk}/posting-preview/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, expected)
        net = [line for line in response.data['lines'] if line['amount_field'] == 'net_amount']
        self.assertEqual(net[0]['debit']['code'], '4100')
        tax = [line for line in response.data['lines'] if line['amount_field'] == 'tax_amount']
        self.assertEqual(tax[0]['debit']['code'], '1400')
        self.assertTrue(response.data['can_approve'])

    def test_preview_matches_posted_journal(self):
        expense = self._expense(
            category=self.telekom,
            expense_account=self.account_4123,
            expense_account_source=ExpenseAccountSource.MANUAL_OVERRIDE,
        )
        preview = self.client.get(f'/api/finance/expenses/{expense.pk}/posting-preview/')
        self.assertEqual(preview.status_code, 200)
        entry = post_document(self.tenant, expense, 'expense_approved', self.owner)
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

    def test_patch_draft_category_and_override(self):
        expense = self._expense()
        patched = self.client.patch(
            f'/api/finance/expenses/{expense.pk}/',
            {'category_id': self.telekom.pk, 'expense_account_id': self.account_4123.pk},
            format='json',
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.data['category_id'], self.telekom.pk)
        self.assertEqual(patched.data['expense_account_id'], self.account_4123.pk)
        self.assertEqual(patched.data['expense_account_source'], 'manual_override')
        preview = self.client.get(f'/api/finance/expenses/{expense.pk}/posting-preview/')
        net = [line for line in preview.data['lines'] if line['amount_field'] == 'net_amount']
        self.assertEqual(net[0]['debit']['code'], '4123')

    def test_patch_clears_override(self):
        expense = self._expense(
            category=self.telekom,
            expense_account=self.account_4123,
            expense_account_source=ExpenseAccountSource.MANUAL_OVERRIDE,
        )
        patched = self.client.patch(
            f'/api/finance/expenses/{expense.pk}/',
            {'expense_account_id': None},
            format='json',
        )
        self.assertEqual(patched.status_code, 200)
        self.assertIsNone(patched.data['expense_account_id'])
        self.assertEqual(patched.data['expense_account_source'], 'category_default')
        preview = self.client.get(f'/api/finance/expenses/{expense.pk}/posting-preview/')
        net = [line for line in preview.data['lines'] if line['amount_field'] == 'net_amount']
        self.assertEqual(net[0]['debit']['code'], '4100')

    def test_patch_approved_is_conflict(self):
        expense = self._expense(status='approved')
        response = self.client.patch(
            f'/api/finance/expenses/{expense.pk}/',
            {'category_id': self.telekom.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'not_draft')

    def test_viewer_can_preview_but_not_patch(self):
        expense = self._expense(category=self.telekom)
        viewer = self._client(self.viewer)
        preview = viewer.get(f'/api/finance/expenses/{expense.pk}/posting-preview/')
        self.assertEqual(preview.status_code, 200)
        patched = viewer.patch(
            f'/api/finance/expenses/{expense.pk}/',
            {'category_id': self.telekom.pk},
            format='json',
        )
        self.assertEqual(patched.status_code, 404)

    def test_cross_tenant_category_rejected(self):
        other_cat = ExpenseCategory.all_objects.create(tenant=self.other, name='X')
        expense = self._expense()
        response = self.client.patch(
            f'/api/finance/expenses/{expense.pk}/',
            {'category_id': other_cat.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_posting_preview_helper_equals_builder_serialize(self):
        expense = self._expense(category=self.telekom)
        dto = posting_preview(tenant=self.tenant, expense_id=expense.pk)
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        self.assertEqual(dto, serialize_document_posting_plan(expense, plan))
        self.assertEqual(dto['lines'][0]['debit']['id'], plan.lines[0].debit_account.pk)

    def test_remember_confirm_inputs_saves_category_not_account(self):
        from domains.finance.services.posting_suggestions import resolve_confirm_posting_inputs

        category, account, source = resolve_confirm_posting_inputs(
            tenant=self.tenant,
            partner=self.supplier,
            category_id=self.telekom.pk,
            expense_account_id=self.account_4123.pk,
            remember_category_for_partner=True,
        )
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.default_expense_category_id, self.telekom.pk)
        self.assertEqual(account.pk, self.account_4123.pk)
        self.assertEqual(source, 'manual_override')
        self.assertEqual(category.pk, self.telekom.pk)
