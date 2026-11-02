"""Document list filters: posted cost_center vs operational fixed_asset."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounting.models import (
    CostCenter,
    CostCenterKind,
    DepreciationMethod,
    FixedAsset,
    FixedAssetOrigin,
    FixedAssetStatus,
    OfficialDocument,
    Vehicle,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import ensure_default_posting_rules, post_document
from accounting.services.rrif_import import import_rrif_chart
from domains.assets.services.vehicle import link_vehicle_to_fixed_asset
from expenses.models import Expense, ExpenseCategory, ExpenseSource
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

HOST = 'docdim.racunai.hr'
OTHER_HOST = 'docdim2.racunai.hr'


@override_settings(
    ALLOWED_HOSTS=[HOST, OTHER_HOST, 'testserver'],
    TENANT_PLATFORM_DOMAIN='racunai.hr',
    TENANT_STAGE_INFIX='',
    TENANT_RESERVED_SLUGS=['app', 'admin', 'www', 'api'],
    SECURE_SSL_REDIRECT=False,
)
class DocumentDimensionFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='docdim', name='Doc Dim')
        cls.other = Tenant.objects.create(slug='docdim2', name='Other')
        provision_tenant_chart(cls.tenant)
        provision_tenant_chart(cls.other)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.owner = User.objects.create_user(username='docdim-owner', password='test')
        cls.other_user = User.objects.create_user(username='docdim-other', password='test')
        TenantMembership.objects.create(user=cls.owner, tenant=cls.tenant, role='owner')
        TenantMembership.objects.create(user=cls.other_user, tenant=cls.other, role='owner')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljač',
            partner_type='supplier',
            status='active',
            address='U',
            city='Zagreb',
            postal_code='10000',
        )
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.kitchen = CostCenter.all_objects.create(
            tenant=cls.tenant,
            code='110',
            name='Kuhinja',
            kind=CostCenterKind.LOCATION,
        )
        cls.audi_cc = CostCenter.all_objects.create(
            tenant=cls.tenant,
            code='701',
            name='Audi A8',
            kind=CostCenterKind.OBJECT,
        )
        from accounting.models import ChartOfAccounts

        cls.account_4100 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='4100')
        cls.account_0373 = ChartOfAccounts.all_objects.get(tenant=cls.tenant, account_code='0373')
        cls.asset = FixedAsset.all_objects.create(
            tenant=cls.tenant,
            name='Audi A8 Lang 50 TDI',
            vin='WAUZZZF86RN003268',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.OPENING_BALANCE,
            acquisition_cost=Decimal('100.00'),
            purchase_date=date(2026, 8, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=cls.account_0373,
            asset_account=cls.account_0373,
            accumulated_depreciation_account=cls.account_0373,
            depreciation_expense_account=cls.account_4100,
            cost_center=cls.audi_cc,
        )
        cls.vehicle = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='Audi A8 Lang 50 TDI',
            vin='WAUZZZF86RN003268',
            cost_center=cls.audi_cc,
        )
        link_vehicle_to_fixed_asset(cls.vehicle, cls.asset)
        cls.other_asset = FixedAsset.all_objects.create(
            tenant=cls.tenant,
            name='VW Golf',
            vin='WVWZZZCD8PW153457',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.OPENING_BALANCE,
            acquisition_cost=Decimal('50.00'),
            purchase_date=date(2026, 5, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=cls.account_0373,
            asset_account=cls.account_0373,
            accumulated_depreciation_account=cls.account_0373,
            depreciation_expense_account=cls.account_4100,
        )
        cls.other_vehicle = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='VW Golf',
            vin='WVWZZZCD8PW153457',
        )
        link_vehicle_to_fixed_asset(cls.other_vehicle, cls.other_asset)

    def setUp(self):
        self.client = APIClient()
        token = RefreshToken.for_user(self.owner).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.client.defaults['HTTP_HOST'] = HOST

    def _expense(self, **overrides):
        defaults = {
            'tenant': self.tenant,
            'expense_number': f'DD-{Expense.all_objects.filter(tenant=self.tenant).count() + 1}',
            'source': ExpenseSource.MANUAL,
            'status': 'draft',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('100.00'),
            'tax_amount': Decimal('25.00'),
            'currency': 'EUR',
            'expense_date': date(2026, 8, 1),
            'description': 'MT filter',
            'created_by': self.owner,
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def _official(self, **overrides):
        defaults = {
            'tenant': self.tenant,
            'kind': OfficialDocument.KIND_TAX_DECISION,
            'issuer': self.supplier,
            'document_number': f'UP/{OfficialDocument.all_objects.filter(tenant=self.tenant).count() + 1}',
            'issue_date': date(2026, 8, 3),
            'amount': Decimal('10.00'),
            'currency': 'EUR',
            'status': OfficialDocument.STATUS_REGISTERED,
            'created_by': self.owner,
        }
        defaults.update(overrides)
        return OfficialDocument.all_objects.create(**defaults)

    def test_cost_center_filter_is_posted_journal_line_only(self):
        posted = self._expense(cost_center=self.kitchen)
        post_document(self.tenant, posted, 'expense_approved', self.owner)
        draft = self._expense(cost_center=self.kitchen, expense_number='DD-DRAFT')
        other = self._expense(cost_center=self.audi_cc)
        post_document(self.tenant, other, 'expense_approved', self.owner)

        body = self.client.get(f'/api/documents/?cost_center={self.kitchen.pk}').json()
        ids = {row['id'] for row in body['results']}
        self.assertIn(posted.pk, ids)
        self.assertNotIn(draft.pk, ids)
        self.assertNotIn(other.pk, ids)

        outsider = APIClient()
        token = RefreshToken.for_user(self.other_user).access_token
        outsider.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        outsider.defaults['HTTP_HOST'] = OTHER_HOST
        hidden = outsider.get(f'/api/documents/?cost_center={self.kitchen.pk}').json()
        self.assertEqual(hidden['count'], 0)

    def test_fixed_asset_filter_uses_vehicle_and_related_asset(self):
        on_audi = self._expense(vehicle=self.vehicle)
        on_golf = self._expense(vehicle=self.other_vehicle)
        ppmv = self._official(related_fixed_asset=self.asset)
        other_od = self._official(related_fixed_asset=self.other_asset)

        body = self.client.get(f'/api/documents/?fixed_asset={self.asset.pk}').json()
        ids = {(row['direction'], row['id']) for row in body['results']}
        self.assertIn(('incoming', on_audi.pk), ids)
        self.assertIn(('official', ppmv.pk), ids)
        self.assertNotIn(('incoming', on_golf.pk), ids)
        self.assertNotIn(('official', other_od.pk), ids)
