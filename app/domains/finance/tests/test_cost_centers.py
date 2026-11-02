"""Cost centers — codebook, posting invariant, report, reverse copy."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import ChartOfAccounts, CostCenter, CostCenterKind
from accounting.services.chart import provision_tenant_chart
from accounting.services.journal_lines import validate_cost_center_for_account
from accounting.services.posting import ensure_default_posting_rules, post_document
from accounting.services.reports import cost_center_report
from accounting.services.rrif_import import import_rrif_chart
from domains.finance.services.cost_centers import ensure_default_cost_centers
from expenses.models import Expense, ExpenseSource
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

HOST = 'cc.racunai.hr'
OTHER_HOST = 'cc2.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class CostCenterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='cc', name='CC')
        cls.other = Tenant.objects.create(slug='cc2', name='Other')
        provision_tenant_chart(cls.tenant)
        provision_tenant_chart(cls.other)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.owner = User.objects.create_user(username='cc-owner', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljač',
            partner_type='supplier',
            status='active',
            address='U',
            city='Zagreb',
            postal_code='10000',
        )
        from expenses.models import ExpenseCategory

        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.account_4100 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4100')
        cls.account_2201 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='2201')
        cls.kitchen = CostCenter.all_objects.create(
            tenant=cls.tenant,
            code='110',
            name='Kuhinja',
            kind=CostCenterKind.LOCATION,
        )
        cls.group = CostCenter.all_objects.create(
            tenant=cls.tenant,
            code='1',
            name='Ugostiteljstvo',
            kind=CostCenterKind.GROUP,
        )

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = HOST

    def _expense(self, **overrides) -> Expense:
        defaults = {
            'tenant': self.tenant,
            'expense_number': f'CC-{Expense.all_objects.filter(tenant=self.tenant).count() + 1}',
            'source': ExpenseSource.MANUAL,
            'status': 'draft',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('100.00'),
            'tax_amount': Decimal('25.00'),
            'currency': 'EUR',
            'expense_date': date(2026, 8, 1),
            'description': 'MT test',
            'created_by': self.owner,
            'cost_center': self.kitchen,
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def test_validator_rejects_balance_sheet_and_empty_class(self):
        with self.assertRaises(ValidationError):
            validate_cost_center_for_account(self.account_2201, self.kitchen)
        custom = ChartOfAccounts.all_objects.create(
            tenant=self.tenant,
            account_code='X999',
            account_name='Custom empty class',
            account_class='',
            account_type=self.account_4100.account_type,
            is_postable=True,
            is_synthetic=False,
            is_active=True,
        )
        with self.assertRaises(ValidationError):
            validate_cost_center_for_account(custom, self.kitchen)
        validate_cost_center_for_account(self.account_4100, self.kitchen)

    def test_validator_rejects_group(self):
        with self.assertRaises(ValidationError):
            validate_cost_center_for_account(self.account_4100, self.group)

    def test_persist_and_post_document_puts_mt_only_on_rdg(self):
        expense = self._expense()
        entry = post_document(self.tenant, expense, 'expense_approved', self.owner)
        self.assertIsNotNone(entry)
        lines = list(entry.lines.select_related('account', 'cost_center'))
        rdg = [line for line in lines if line.account.account_class in {'4', '5', '7'}]
        balance = [line for line in lines if line.account.account_class not in {'4', '5', '7'}]
        self.assertTrue(rdg)
        self.assertTrue(all(line.cost_center_id == self.kitchen.pk for line in rdg))
        self.assertTrue(all(line.cost_center_id is None for line in balance))

    def test_reverse_copies_cost_center(self):
        expense = self._expense()
        entry = post_document(self.tenant, expense, 'expense_approved', self.owner)
        reversal = entry.reverse(self.owner)
        original_mt = {
            (line.account_id, line.cost_center_id, line.debit_amount, line.credit_amount)
            for line in entry.lines.all()
        }
        reversed_mt = {
            (line.account_id, line.cost_center_id, line.credit_amount, line.debit_amount)
            for line in reversal.lines.all()
        }
        self.assertEqual(original_mt, reversed_mt)

    def test_locked_expense_cost_center_after_approve(self):
        expense = self._expense()
        expense.status = 'approved'
        expense.save(update_fields=['status'])
        expense.cost_center = None
        with self.assertRaises(ValidationError):
            expense.save()

    def test_posted_lines_survive_source_mt_queryset_update(self):
        expense = self._expense()
        entry = post_document(self.tenant, expense, 'expense_approved', self.owner)
        Expense.all_objects.filter(pk=expense.pk).update(cost_center=None)
        rdg = [
            line
            for line in entry.lines.select_related('account')
            if line.account.account_class in {'4', '5', '7'}
        ]
        self.assertTrue(rdg)
        self.assertTrue(all(line.cost_center_id == self.kitchen.pk for line in rdg))

    def test_official_document_and_invoice_cost_center_locked_after_post(self):
        from accounting.models import OfficialDocument, OfficialDocumentPostingProfile
        from accounting.services.posting import OFFICIAL_DOCUMENT_POSTED
        from invoices.models import Invoice

        profile = OfficialDocumentPostingProfile.all_objects.get(
            tenant=self.tenant,
            code='administrative_fee',
        )
        document = OfficialDocument.all_objects.create(
            tenant=self.tenant,
            kind=OfficialDocument.KIND_OTHER,
            issuer=self.supplier,
            document_number='MT-OD-1',
            issue_date=date(2026, 8, 1),
            amount=Decimal('50.00'),
            currency='EUR',
            status=OfficialDocument.STATUS_REGISTERED,
            posting_profile=profile,
            cost_center=self.kitchen,
        )
        entry = post_document(self.tenant, document, OFFICIAL_DOCUMENT_POSTED, self.owner)
        self.assertIsNotNone(entry)
        document.cost_center = None
        with self.assertRaises(ValidationError):
            document.save()

        customer = Partner.all_objects.create(
            tenant=self.tenant,
            name='Kupac',
            partner_type='customer',
            status='active',
            address='U',
            city='Zagreb',
            postal_code='10000',
        )
        invoice = Invoice.all_objects.create(
            tenant=self.tenant,
            status='draft',
            company_to=customer,
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 15),
            subtotal=Decimal('100.00'),
            tax_amount=Decimal('25.00'),
            total_amount=Decimal('125.00'),
            cost_center=self.kitchen,
        )
        posted = post_document(self.tenant, invoice, 'invoice_issued', self.owner)
        self.assertIsNotNone(posted)
        invoice.cost_center = None
        with self.assertRaises(ValidationError):
            invoice.save()

    def test_report_groups_by_cost_center(self):
        expense = self._expense()
        post_document(self.tenant, expense, 'expense_approved', self.owner)
        data = cost_center_report(self.tenant, 2026, 8, cumulative=True)
        kitchen = next(row for row in data['results'] if row['code'] == '110')
        self.assertEqual(kitchen['name'], 'Kuhinja')
        self.assertNotEqual(kitchen['total'], '0.00')

    def test_api_crud_and_report(self):
        listed = self.client.get('/api/finance/cost-centers/')
        self.assertEqual(listed.status_code, 200)
        codes = {row['code'] for row in listed.data['results']}
        self.assertIn('110', codes)
        created = self.client.post(
            '/api/finance/cost-centers/',
            {'code': '300', 'name': 'Vinoteka', 'kind': 'location'},
            format='json',
        )
        self.assertEqual(created.status_code, 201)
        nested = self.client.post(
            '/api/finance/cost-centers/',
            {'code': '301', 'name': 'Pod-vinoteka', 'kind': 'location', 'parent_id': created.data['id']},
            format='json',
        )
        self.assertEqual(nested.status_code, 400)
        report = self.client.get('/api/finance/reports/cost-centers/?year=2026&month=8')
        self.assertEqual(report.status_code, 200)
        self.assertIn('results', report.data)

    def test_seed_hospitality_is_idempotent(self):
        first = ensure_default_cost_centers(self.tenant, preset='hospitality')
        second = ensure_default_cost_centers(self.tenant, preset='hospitality')
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)

    def test_tenant_isolation(self):
        other_client = APIClient()
        User = get_user_model()
        other_user = User.objects.create_user(username='cc-other', password='test')
        TenantMembership.objects.create(user=other_user, tenant=self.other, role='owner')
        token = RefreshToken.for_user(other_user).access_token
        other_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        other_client.defaults['HTTP_HOST'] = OTHER_HOST
        listed = other_client.get('/api/finance/cost-centers/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data['count'], 0)

    def test_retrieve_includes_vehicle_and_fixed_assets(self):
        from accounting.models import (
            DepreciationMethod,
            FixedAsset,
            FixedAssetOrigin,
            FixedAssetStatus,
            Vehicle,
        )

        retrieved = self.client.get(f'/api/finance/cost-centers/{self.kitchen.pk}/')
        self.assertEqual(retrieved.status_code, 200)
        self.assertEqual(retrieved.data['code'], '110')
        self.assertIsNone(retrieved.data['vehicle'])
        self.assertEqual(retrieved.data['fixed_assets'], [])

        missing = self.client.get('/api/finance/cost-centers/999999/')
        self.assertEqual(missing.status_code, 404)

        object_cc = CostCenter.all_objects.create(
            tenant=self.tenant,
            code='701',
            name='Audi A8',
            kind=CostCenterKind.OBJECT,
            parent=self.group,
        )
        asset = FixedAsset.all_objects.create(
            tenant=self.tenant,
            name='Audi A8 Lang 50 TDI',
            vin='WAUZZZF86RN003268',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.OPENING_BALANCE,
            acquisition_cost=Decimal('100.00'),
            purchase_date=date(2026, 8, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=self.account_4100,
            asset_account=self.account_4100,
            accumulated_depreciation_account=self.account_4100,
            depreciation_expense_account=self.account_4100,
            cost_center=object_cc,
        )
        Vehicle.all_objects.create(
            tenant=self.tenant,
            name='Audi A8 Lang 50 TDI',
            vin='WAUZZZF86RN003268',
            fixed_asset=asset,
            cost_center=object_cc,
        )
        body = self.client.get(f'/api/finance/cost-centers/{object_cc.pk}/').json()
        self.assertEqual(body['vehicle']['vin'], 'WAUZZZF86RN003268')
        self.assertEqual(body['vehicle']['fixed_asset_id'], asset.pk)
        self.assertEqual(body['fixed_assets'], [{'id': asset.pk, 'name': 'Audi A8 Lang 50 TDI'}])
