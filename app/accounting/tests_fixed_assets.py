"""Testovi modula osnovnih sredstava — seed: T-Cross temeljnica #49 (Fine Star)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import ChartOfAccounts, FixedAsset, FixedAssetOrigin, FixedAssetStatus, JournalEntry
from accounting.services.chart import provision_tenant_chart
from accounting.services.fixed_assets import create_fixed_asset_from_purchase, extract_vin_from_journal_entry
from accounting.services.manual_journal import JournalLineInput, create_manual_journal_entry
from accounting.services.rrif_import import import_rrif_chart
from tenants.models import Tenant

# Stvarni podaci s Fine Star T-Cross faze 1 (JournalEntry id=49, broj 202605-0011).
TCROSS_VIN = 'WVGZZZC1ZPY022544'
TCROSS_REFERENCE = f'70025237|{TCROSS_VIN}|ZOU497136235'
TCROSS_ACQUISITION_COST = Decimal('8000.00')
TCROSS_PURCHASE_DATE = date(2026, 5, 22)


class FixedAssetFromPurchaseTests(TestCase):
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

        self.purchase_journal_entry = create_manual_journal_entry(
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

    def test_extract_vin_from_reference(self):
        self.assertEqual(
            extract_vin_from_journal_entry(self.purchase_journal_entry),
            TCROSS_VIN,
        )

    def test_create_fixed_asset_from_purchase_tcros_seed(self):
        """Replika JournalEntry #49 — bez ponovnog upisa nabavnih podataka."""
        asset = create_fixed_asset_from_purchase(
            purchase_journal_entry=self.purchase_journal_entry,
            construction_account=self.construction_account,
            asset_account=self.asset_account,
            accumulated_depreciation_account=self.accumulated_depreciation_account,
            depreciation_expense_account=self.depreciation_expense_account,
            name='VW T-Cross',
        )

        self.assertEqual(asset.purchase_journal_entry_id, self.purchase_journal_entry.pk)
        self.assertEqual(asset.acquisition_cost, TCROSS_ACQUISITION_COST)
        self.assertEqual(asset.purchase_date, TCROSS_PURCHASE_DATE)
        self.assertEqual(asset.status, FixedAssetStatus.IN_PREPARATION)
        self.assertEqual(asset.origin, FixedAssetOrigin.PURCHASE)
        self.assertEqual(asset.vin, TCROSS_VIN)
        self.assertEqual(asset.name, 'VW T-Cross')
        self.assertIsNone(asset.in_service_date)
        self.assertIsNone(asset.activation_journal_entry_id)
        self.assertEqual(asset.accumulated_depreciation, Decimal('0.00'))
        self.assertEqual(asset.current_book_value, TCROSS_ACQUISITION_COST)
        self.assertEqual(asset.construction_account.account_code, '0373')
        self.assertEqual(asset.asset_account.account_code, '032001')

    def test_idempotent_for_same_purchase_journal_entry(self):
        first = create_fixed_asset_from_purchase(
            purchase_journal_entry=self.purchase_journal_entry,
            construction_account=self.construction_account,
            asset_account=self.asset_account,
            accumulated_depreciation_account=self.accumulated_depreciation_account,
            depreciation_expense_account=self.depreciation_expense_account,
            name='VW T-Cross',
        )
        second = create_fixed_asset_from_purchase(
            purchase_journal_entry=self.purchase_journal_entry,
            construction_account=self.construction_account,
            asset_account=self.asset_account,
            accumulated_depreciation_account=self.accumulated_depreciation_account,
            depreciation_expense_account=self.depreciation_expense_account,
            name='Duplikat',
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.name, 'VW T-Cross')
        self.assertEqual(FixedAsset.all_objects.filter(purchase_journal_entry=self.purchase_journal_entry).count(), 1)

    def test_rejects_draft_journal_entry_via_service(self):
        draft = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0099',
            entry_date=TCROSS_PURCHASE_DATE,
            status='draft',
            description='Nacrt',
            created_by=self.user,
        )
        with self.assertRaisesMessage(Exception, 'mora biti knjižena'):
            create_fixed_asset_from_purchase(
                purchase_journal_entry=draft,
                construction_account=self.construction_account,
                asset_account=self.asset_account,
                accumulated_depreciation_account=self.accumulated_depreciation_account,
                depreciation_expense_account=self.depreciation_expense_account,
            )

    def test_rejects_draft_journal_entry_via_model_clean(self):
        draft = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202605-0098',
            entry_date=TCROSS_PURCHASE_DATE,
            status='draft',
            description='Nacrt',
            created_by=self.user,
        )
        asset = FixedAsset(
            tenant=self.tenant,
            name='Draft test',
            acquisition_cost=TCROSS_ACQUISITION_COST,
            purchase_date=TCROSS_PURCHASE_DATE,
            construction_account=self.construction_account,
            asset_account=self.asset_account,
            accumulated_depreciation_account=self.accumulated_depreciation_account,
            depreciation_expense_account=self.depreciation_expense_account,
            purchase_journal_entry=draft,
        )
        with self.assertRaisesMessage(ValidationError, 'mora biti knjižena'):
            asset.full_clean()
