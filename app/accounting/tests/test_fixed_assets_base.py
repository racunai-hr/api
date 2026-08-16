"""Shared fixtures za testove osnovnih sredstava (T-Cross + Golf seed)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model

from accounting.models import ChartOfAccounts, FixedAssetStatus
from accounting.services.analytics import get_or_create_analytic_for_payer
from accounting.services.chart import provision_tenant_chart
from accounting.services.fixed_assets import create_fixed_asset_from_purchase
from accounting.services.manual_journal import JournalLineInput, create_manual_journal_entry
from accounting.services.rrif_import import import_rrif_chart
from domains.assets.services.activation import activate_fixed_asset
from expenses.models import ExpensePayer
from tenants.models import Tenant

TCROSS_VIN = 'WVGZZZC1ZPY022544'
TCROSS_REFERENCE = f'70025237|{TCROSS_VIN}|ZOU497136235'
TCROSS_ACQUISITION_COST = Decimal('8000.00')
TCROSS_PURCHASE_DATE = date(2026, 5, 22)
TCROSS_IN_SERVICE_DATE = date(2026, 7, 1)

GOLF_VIN = 'WVWZZZCD8PW153457'
GOLF_REFERENCE = f'70025249|{GOLF_VIN}|ZOU497136235'
GOLF_ACQUISITION_COST = Decimal('15882.35')
GOLF_PURCHASE_DATE = date(2026, 5, 27)
GOLF_IN_SERVICE_DATE = date(2026, 7, 15)


class FixedAssetSeedMixin:
    def setUp(self):
        import_rrif_chart(clear=True)
        self.tenant, _ = Tenant.objects.get_or_create(
            slug='finestar',
            defaults={'name': 'Fine Star d.o.o.'},
        )
        provision_tenant_chart(self.tenant)
        User = get_user_model()
        self.user = User.objects.create_user(username='fa-user', password='test')

        self.construction_account = ChartOfAccounts.all_objects.get(
            tenant=self.tenant, account_code='0373',
        )
        self.asset_account = ChartOfAccounts.all_objects.get(
            tenant=self.tenant, account_code='032001',
        )
        self.accumulated_depreciation_account = ChartOfAccounts.all_objects.get(
            tenant=self.tenant, account_code='0393',
        )
        self.depreciation_expense_account = ChartOfAccounts.all_objects.get(
            tenant=self.tenant, account_code='4314',
        )

        self.tcross_purchase_journal_entry = create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=TCROSS_PURCHASE_DATE,
            description='T-Cross faza 1 — nabava vozila u pripremi',
            reference=TCROSS_REFERENCE,
            lines=[
                JournalLineInput(account_code='0373', debit=TCROSS_ACQUISITION_COST, credit=Decimal('0')),
                JournalLineInput(account_code='1000', debit=Decimal('0'), credit=TCROSS_ACQUISITION_COST),
            ],
            post=True,
        )

    def create_tcross_asset(self, *, in_service_date=TCROSS_IN_SERVICE_DATE):
        asset = create_fixed_asset_from_purchase(
            purchase_journal_entry=self.tcross_purchase_journal_entry,
            construction_account=self.construction_account,
            asset_account=self.asset_account,
            accumulated_depreciation_account=self.accumulated_depreciation_account,
            depreciation_expense_account=self.depreciation_expense_account,
            name='VW T-Cross',
        )
        if in_service_date:
            asset.in_service_date = in_service_date
            asset.save(update_fields=['in_service_date', 'updated_at'])
        return asset

    def create_golf_asset(self, *, in_service_date=GOLF_IN_SERVICE_DATE):
        payer, _ = ExpensePayer.all_objects.get_or_create(
            tenant=self.tenant,
            oib='11528564544',
            defaults={'name': 'Ante Vrcan'},
        )
        payer_account = get_or_create_analytic_for_payer(self.tenant, payer)
        purchase_journal_entry = create_manual_journal_entry(
            self.tenant,
            self.user,
            entry_date=GOLF_PURCHASE_DATE,
            description='Golf faza 1 — nabava vozila u pripremi',
            reference=GOLF_REFERENCE,
            lines=[
                JournalLineInput(account_code='0373', debit=GOLF_ACQUISITION_COST, credit=Decimal('0')),
                JournalLineInput(
                    account_code=payer_account.account_code,
                    debit=Decimal('0'),
                    credit=GOLF_ACQUISITION_COST,
                ),
            ],
            post=True,
        )
        asset = create_fixed_asset_from_purchase(
            purchase_journal_entry=purchase_journal_entry,
            construction_account=self.construction_account,
            asset_account=self.asset_account,
            accumulated_depreciation_account=self.accumulated_depreciation_account,
            depreciation_expense_account=self.depreciation_expense_account,
            name='VW Golf',
            vin=GOLF_VIN,
        )
        if in_service_date:
            asset.in_service_date = in_service_date
            asset.save(update_fields=['in_service_date', 'updated_at'])
        return asset

    def activate_asset(self, asset, *, in_service_date=None):
        return activate_fixed_asset(asset, self.user, in_service_date=in_service_date)
