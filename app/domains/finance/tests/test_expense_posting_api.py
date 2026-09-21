"""Finance/purchasing APIs for expense posting inputs and preview."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import ChartOfAccounts, CostCenter, CostCenterKind, JournalEntryLine
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import build_document_posting_plan, ensure_default_posting_rules, post_document
from accounting.services.rrif_import import import_rrif_chart
from domains.finance.services.expenses import posting_preview, serialize_document_posting_plan
from expenses.models import Expense, ExpenseAccountSource, ExpenseCategory, ExpenseLine, ExpenseSource
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
        cls.account_1909 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='1909')
        cls.account_1900 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='1900')
        cls.account_4040 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4040')
        cls.kitchen = CostCenter.all_objects.create(
            tenant=cls.tenant,
            code='110',
            name='Kuhinja',
            kind=CostCenterKind.LOCATION,
        )

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
        by_name = {row['name']: row for row in listed.data['results']}
        self.assertIsNone(by_name['Ostalo']['code'])
        self.assertIsNone(by_name['Telekomunikacije']['code'])
        patched = self.client.patch(
            f'/api/purchasing/expense-categories/{self.ostalo.pk}/',
            {'default_account_id': self.account_4123.pk},
            format='json',
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.data['default_account']['code'], '4123')
        self.ostalo.refresh_from_db()
        self.assertEqual(self.ostalo.default_account_id, self.account_4123.pk)

    def test_expense_categories_expose_code_read_only(self):
        category = ExpenseCategory.all_objects.create(
            tenant=self.tenant,
            name='Obvezno auto osiguranje',
            code='vehicle_insurance_compulsory',
        )
        listed = self.client.get('/api/purchasing/expense-categories/')
        self.assertEqual(listed.status_code, 200)
        by_id = {row['id']: row for row in listed.data['results']}
        self.assertEqual(by_id[category.pk]['code'], 'vehicle_insurance_compulsory')
        self.assertEqual(by_id[self.ostalo.pk]['code'], None)
        patched = self.client.patch(
            f'/api/purchasing/expense-categories/{category.pk}/',
            {'code': 'hacked', 'default_account_id': None},
            format='json',
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.data['code'], 'vehicle_insurance_compulsory')
        category.refresh_from_db()
        self.assertEqual(category.code, 'vehicle_insurance_compulsory')
        self.assertIsNone(category.default_account_id)

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

    def _hac_lines(self, expense, *, prepaid_account=None, device_account=None):
        ExpenseLine.all_objects.create(
            expense=expense,
            position=1,
            description='ENC nadoplata',
            net_amount=Decimal('80.00'),
            vat_amount=Decimal('20.00'),
            gross_amount=Decimal('100.00'),
            posting_account=prepaid_account,
        )
        ExpenseLine.all_objects.create(
            expense=expense,
            position=2,
            description='ENC uređaj',
            net_amount=Decimal('24.00'),
            vat_amount=Decimal('6.00'),
            gross_amount=Decimal('30.00'),
            posting_account=device_account,
        )

    def test_header_only_lines_without_accounts_keep_single_net_debit(self):
        expense = self._expense(
            category=self.telekom,
            amount=Decimal('130.00'),
            tax_amount=Decimal('26.00'),
        )
        self._hac_lines(expense)
        preview = self.client.get(f'/api/finance/expenses/{expense.pk}/posting-preview/')
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data['can_approve'])
        net = [line for line in preview.data['lines'] if line['amount_field'] == 'net_amount']
        self.assertEqual(len(net), 1)
        self.assertEqual(net[0]['debit']['code'], '4100')
        self.assertEqual(net[0]['amount'], '104.00')

    def test_split_net_debits_prepaid_and_small_inventory(self):
        expense = self._expense(
            category=self.telekom,
            amount=Decimal('130.00'),
            tax_amount=Decimal('26.00'),
            cost_center=self.kitchen,
        )
        self._hac_lines(
            expense,
            prepaid_account=self.account_1909,
            device_account=self.account_4040,
        )
        preview = self.client.get(f'/api/finance/expenses/{expense.pk}/posting-preview/')
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data['can_approve'])
        net = [line for line in preview.data['lines'] if line['amount_field'] == 'net_amount']
        tax = [line for line in preview.data['lines'] if line['amount_field'] == 'tax_amount']
        self.assertEqual(
            [(line['debit']['code'], line['amount']) for line in net],
            [('1909', '80.00'), ('4040', '24.00')],
        )
        self.assertIsNone(net[0]['debit_cost_center'])
        self.assertEqual(net[1]['debit_cost_center']['code'], '110')
        self.assertEqual(
            [(line['debit']['code'], line['amount']) for line in tax],
            [('1400', '20.00'), ('1400', '6.00')],
        )
        self.assertEqual(net[0]['credit']['code'], tax[0]['credit']['code'])

        entry = post_document(self.tenant, expense, 'expense_approved', self.owner)
        posted = list(
            JournalEntryLine.objects.filter(journal_entry=entry).order_by('id').values_list(
                'account__account_code', 'debit_amount', 'credit_amount', 'cost_center_id',
            )
        )
        planned = []
        for line in preview.data['lines']:
            debit_cc = line['debit_cost_center']['id'] if line['debit_cost_center'] else None
            credit_cc = line['credit_cost_center']['id'] if line['credit_cost_center'] else None
            planned.append((line['debit']['code'], Decimal(line['amount']), Decimal('0'), debit_cc))
            planned.append((line['credit']['code'], Decimal('0'), Decimal(line['amount']), credit_cc))
        self.assertEqual(posted, planned)

    def test_patch_line_accounts_and_mixed_blocks_approve(self):
        expense = self._expense(amount=Decimal('130.00'), tax_amount=Decimal('26.00'))
        self._hac_lines(expense)
        line = expense.lines.get(position=1)
        patched = self.client.patch(
            f'/api/finance/expenses/{expense.pk}/',
            {'line_accounts': [{'position': 1, 'posting_account_id': self.account_1909.pk}]},
            format='json',
        )
        self.assertEqual(patched.status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.posting_account_id, self.account_1909.pk)
        preview = self.client.get(f'/api/finance/expenses/{expense.pk}/posting-preview/')
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.data['can_approve'])
        self.assertIn('Sve stavke moraju imati konto', ' '.join(preview.data['warnings']))
        approved = self.client.post(f'/api/finance/expenses/{expense.pk}/approve/')
        self.assertEqual(approved.status_code, 400)
        self.assertEqual(approved.data['code'], 'mixed_line_accounts')

    def test_split_three_enc_lines_vat_per_line(self):
        expense = self._expense(
            category=self.telekom,
            amount=Decimal('130.00'),
            tax_amount=Decimal('26.00'),
            cost_center=self.kitchen,
        )
        ExpenseLine.all_objects.create(
            expense=expense,
            position=1,
            description='Uplata iznosa - ENC za kat. I',
            net_amount=Decimal('80.00'),
            vat_amount=Decimal('20.00'),
            gross_amount=Decimal('100.00'),
            posting_account=self.account_1900,
        )
        ExpenseLine.all_objects.create(
            expense=expense,
            position=2,
            description='UREDAJ ENC - KOMPLET Prepaid, za kat. I',
            net_amount=Decimal('12.00'),
            vat_amount=Decimal('3.00'),
            gross_amount=Decimal('15.00'),
            posting_account=self.account_4040,
        )
        ExpenseLine.all_objects.create(
            expense=expense,
            position=3,
            description='UREDAJ ENC - KOMPLET Prepaid, za kat. I',
            net_amount=Decimal('12.00'),
            vat_amount=Decimal('3.00'),
            gross_amount=Decimal('15.00'),
            posting_account=self.account_4040,
        )
        preview = self.client.get(f'/api/finance/expenses/{expense.pk}/posting-preview/')
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data['can_approve'])
        net = [line for line in preview.data['lines'] if line['amount_field'] == 'net_amount']
        tax = [line for line in preview.data['lines'] if line['amount_field'] == 'tax_amount']
        self.assertEqual(
            [(line['debit']['code'], line['amount']) for line in net],
            [('1900', '80.00'), ('4040', '12.00'), ('4040', '12.00')],
        )
        self.assertEqual(
            [(line['debit']['code'], line['amount']) for line in tax],
            [('1400', '20.00'), ('1400', '3.00'), ('1400', '3.00')],
        )
        self.assertIsNone(net[0]['debit_cost_center'])
