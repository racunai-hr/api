"""Nabavna vrijednost vozila za daljnju prodaju: D 667-Vn / 6600-Vn, P 2201-P. Iznosi su eksplicitni EUR."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from accounting.models import (
    AnalyticAccount,
    ChartOfAccounts,
    DepreciationMethod,
    FixedAsset,
    FixedAssetOrigin,
    FixedAssetStatus,
    JournalEntry,
    JournalEntryLine,
    Vehicle,
    VehicleAccountingRole,
)
from accounting.services.analytics import (
    get_or_create_analytic_for_partner,
    get_or_create_analytic_for_payer,
    get_or_create_inventory_analytic_for_vehicle,
)
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import build_document_posting_plan, ensure_default_posting_rules
from accounting.services.rrif_import import import_rrif_chart
from domains.assets.services.vehicle import link_vehicle_to_fixed_asset
from domains.assets.services.vehicle_inventory import vehicle_inventory_total, vehicle_inventory_value
from domains.banking.write.expense_close import CloseError, close_expense_from_bank
from domains.finance.services.accounting_review import review_expense
from domains.finance.services.expenses import ExpenseApproveBadRequest, approve_expense_for_posting
from domains.finance.services.vehicle_dependent_cost_capitalization import _inspect_expense
from expenses.models import (
    Expense,
    ExpenseCategory,
    ExpensePayer,
    ExpensePostingProfile,
    InventoryCostComponent,
)
from partners.models import Partner
from tenants.models import Tenant, TenantMembership

FUEL = {
    'purchase_nature': 'fuel',
    'suggested_profile': 'goods_inward_cost',
    'required_context': ['vehicle'],
    'reason': 'Gorivo',
    'confidence': 'high',
}


def _sums(plan):
    debit: dict[str, Decimal] = {}
    credit: dict[str, Decimal] = {}
    for line in plan.lines:
        debit[line.debit_account.account_code] = debit.get(line.debit_account.account_code, Decimal('0')) + line.amount
        credit[line.credit_account.account_code] = credit.get(line.credit_account.account_code, Decimal('0')) + line.amount
    return debit, credit


@override_settings(ACCOUNTING_CLASSIFY_PROVIDER='fake', ACCOUNTING_CLASSIFY_FAKE=FUEL)
class VehicleInventoryCostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='vehicle-inventory', name='Vehicle Inventory')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='vehicle-inventory', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='owner')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Gorivo')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Benzinska',
            partner_type='supplier',
            status='active',
        )
        cls.stock = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='Vozilo na zalihi',
            vin='WDC2479121W000001',
            accounting_role=VehicleAccountingRole.INVENTORY_FOR_RESALE,
        )
        cls.passenger = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='Sluzbeno',
            registration_plate='ZG2002BB',
            accounting_role=VehicleAccountingRole.COMPANY_PASSENGER,
        )
        cls.inv = f'6600-V{cls.stock.pk:05d}'
        cls.supplier_analytic = f'2201-P{cls.supplier.pk:05d}'

    def _expense(self, **overrides):
        number = overrides.pop('expense_number', f'VI-{Expense.all_objects.count() + 1}')
        defaults = {
            'tenant': self.tenant,
            'expense_number': number,
            'status': 'draft',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('98.00'),
            'tax_amount': Decimal('0.00'),
            'expense_date': date(2026, 10, 23),
            'description': 'Gorivo za dopremu',
            'created_by': self.user,
            'vehicle': self.stock,
            'posting_profile': ExpensePostingProfile.VEHICLE_INVENTORY_COST,
            'inventory_cost_component': InventoryCostComponent.FUEL,
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def test_eur_cost_posts_vehicle_inventory_against_supplier(self):
        plan = build_document_posting_plan(self.tenant, self._expense(), 'expense_approved')
        debit, credit = _sums(plan)
        self.assertEqual(debit, {self.inv: Decimal('98.00')})
        self.assertEqual(credit, {self.supplier_analytic: Decimal('98.00')})
        analytic = plan.lines[0].debit_analytic
        self.assertEqual(analytic.vehicle_id, self.stock.pk)
        self.assertEqual(analytic.counterparty_type, '')
        self.assertIsNone(analytic.partner_id)
        self.assertEqual(analytic.synthetic_account.account_code, '6600')
        self.assertIsNone(plan.lines[0].debit_cost_center)

    def test_manual_header_account_does_not_override_inventory(self):
        override = ChartOfAccounts.all_objects.filter(
            tenant=self.tenant, is_postable=True, account_code__startswith='4',
        ).order_by('account_code').first()
        plan = build_document_posting_plan(
            self.tenant,
            self._expense(expense_account=override),
            'expense_approved',
        )
        debit, _credit = _sums(plan)
        self.assertEqual(debit, {self.inv: Decimal('98.00')})

    def test_domestic_vat_stays_deductible_on_inventory_vehicle(self):
        plan = build_document_posting_plan(
            self.tenant,
            self._expense(amount=Decimal('50.00'), tax_amount=Decimal('10.00')),
            'expense_approved',
        )
        debit, credit = _sums(plan)
        self.assertEqual(debit[self.inv], Decimal('40.00'))
        self.assertEqual(debit['1400'], Decimal('10.00'))
        self.assertEqual(credit[self.supplier_analytic], Decimal('50.00'))

    def _foreign(self, treatment):
        return self._expense(
            amount=Decimal('1000.00'),
            tax_amount=Decimal('0.00'),
            currency='DKK',
            foreign_net_amount=Decimal('800.00'),
            foreign_vat_amount=Decimal('200.00'),
            foreign_gross_amount=Decimal('1000.00'),
            foreign_vat_country='DK',
            foreign_vat_rate=Decimal('25.00'),
            foreign_vat_treatment=treatment,
            fx_rate=Decimal('10.000000'),
            fx_rate_source='manual',
            fx_rate_date=date(2026, 10, 23),
        )

    def test_foreign_vat_expense_puts_full_eur_gross_on_inventory(self):
        plan = build_document_posting_plan(self.tenant, self._foreign('expense'), 'expense_approved')
        debit, credit = _sums(plan)
        self.assertEqual(debit, {self.inv: Decimal('100.00')})
        self.assertEqual(credit, {self.supplier_analytic: Decimal('100.00')})

    def test_foreign_vat_refund_splits_inventory_and_receivable(self):
        plan = build_document_posting_plan(self.tenant, self._foreign('refund_receivable'), 'expense_approved')
        debit, credit = _sums(plan)
        self.assertEqual(debit, {self.inv: Decimal('80.00'), '1274': Decimal('20.00')})
        self.assertEqual(credit, {self.supplier_analytic: Decimal('100.00')})

    def test_missing_vehicle_never_posts(self):
        expense = self._expense(vehicle=None)
        with self.assertRaises(ValidationError):
            build_document_posting_plan(self.tenant, expense, 'expense_approved')
        with self.assertRaises(ExpenseApproveBadRequest) as caught:
            approve_expense_for_posting(tenant=self.tenant, expense_id=expense.pk, user=self.user)
        self.assertEqual(caught.exception.code, 'missing_vehicle')
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'draft')
        self.assertFalse(JournalEntry.all_objects.filter(tenant=self.tenant, reference=expense.expense_number).exists())

    def test_non_inventory_vehicle_cannot_carry_inventory_profile(self):
        with self.assertRaises(ValidationError):
            build_document_posting_plan(
                self.tenant,
                self._expense(vehicle=self.passenger),
                'expense_approved',
            )

    def test_asset_purchase_with_inventory_vehicle_is_rejected(self):
        expense = self._expense(posting_profile=ExpensePostingProfile.ASSET_PURCHASE)
        with self.assertRaises(ValidationError):
            build_document_posting_plan(self.tenant, expense, 'expense_approved')
        with self.assertRaises(ExpenseApproveBadRequest):
            approve_expense_for_posting(tenant=self.tenant, expense_id=expense.pk, user=self.user)
        result = review_expense(
            self.tenant,
            self._expense(posting_profile=ExpensePostingProfile.OPEX),
            {'posting_profile': 'asset_purchase'},
            self.user,
        )
        self.assertEqual(result['status'], 'needs_information')
        self.assertIsNone(result['preview'])

    def test_review_maps_inventory_vehicle_and_previews_inventory_account(self):
        expense = self._expense(posting_profile=ExpensePostingProfile.OPEX)
        result = review_expense(self.tenant, expense, None, self.user)
        self.assertEqual(result['posting_profile'], 'vehicle_inventory_cost')
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['line_accounts'], [])
        debit_codes = {row['account_code'] for row in result['preview'] if Decimal(row['debit']) > 0}
        self.assertEqual(debit_codes, {self.inv})

    def test_manual_line_account_is_rejected_for_inventory(self):
        expense = self._expense(posting_profile=ExpensePostingProfile.OPEX)
        account = ChartOfAccounts.all_objects.filter(
            tenant=self.tenant, is_postable=True, account_code__startswith='4',
        ).order_by('account_code').first()
        with self.assertRaises(CloseError) as caught:
            close_expense_from_bank(
                tenant=self.tenant,
                user=self.user,
                expense_id=expense.pk,
                bank_transaction_id=1,
                lines=[{'position': 1, 'account_id': account.pk}],
                remember=False,
                idempotency_key='inventory-manual',
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn('zalihi', caught.exception.detail)

    def test_value_is_posted_balance_and_storno_returns_to_zero(self):
        expense = self._expense()
        approve_expense_for_posting(tenant=self.tenant, expense_id=expense.pk, user=self.user)
        value = vehicle_inventory_value(self.tenant, self.stock)
        self.assertEqual(value['total'], '98.00')
        self.assertEqual(value['by_analytic'], [{'account_code': self.inv, 'amount': '98.00'}])
        self.assertEqual(value['by_component'], [{'component': 'fuel', 'amount': '98.00'}])
        self.assertEqual(value['by_source'][0]['source_id'], expense.pk)
        entry = JournalEntry.all_objects.get(
            tenant=self.tenant,
            status='posted',
            lines__analytic_account__vehicle=self.stock,
        )
        entry.reverse(self.user)
        self.assertEqual(vehicle_inventory_total(self.tenant, self.stock), Decimal('0.00'))
        self.assertEqual(vehicle_inventory_value(self.tenant, self.stock)['total'], '0.00')

    def test_goods_in_transit_and_warehouse_coexist_and_sum(self):
        transit = get_or_create_inventory_analytic_for_vehicle(self.tenant, self.stock, '667')
        warehouse = get_or_create_inventory_analytic_for_vehicle(self.tenant, self.stock, '6600')
        self.assertNotEqual(transit.pk, warehouse.pk)
        self.assertEqual(transit.account_code, f'667-V{self.stock.pk:05d}')
        self.assertEqual(
            get_or_create_inventory_analytic_for_vehicle(self.tenant, self.stock, '667').pk,
            transit.pk,
        )
        entry = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202610-INV-1',
            entry_date=date(2026, 10, 20),
            description='Kupovna cijena',
            status='posted',
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=transit.chart_account,
            analytic_account=transit,
            debit_amount=Decimal('20000.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=warehouse.chart_account,
            analytic_account=warehouse,
            debit_amount=Decimal('98.00'),
        )
        self.assertEqual(vehicle_inventory_total(self.tenant, self.stock), Decimal('20098.00'))
        self.assertEqual(
            {row['account_code']: row['amount'] for row in vehicle_inventory_value(self.tenant, self.stock)['by_analytic']},
            {transit.account_code: '20000.00', warehouse.account_code: '98.00'},
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnalyticAccount.all_objects.create(
                tenant=self.tenant,
                synthetic_account=warehouse.synthetic_account,
                account_code='6600-DUP',
                account_name='dup',
                counterparty_type='',
                vehicle=self.stock,
            )

    def test_inventory_resolver_refuses_other_synthetics_and_roles(self):
        with self.assertRaises(ValidationError):
            get_or_create_inventory_analytic_for_vehicle(self.tenant, self.stock, '6514')
        with self.assertRaises(ValidationError):
            get_or_create_inventory_analytic_for_vehicle(self.tenant, self.passenger, '6600')

    def test_counterparty_resolvers_never_return_vehicle_analytic(self):
        get_or_create_inventory_analytic_for_vehicle(self.tenant, self.stock, '6600')
        supplier = get_or_create_analytic_for_partner(self.tenant, self.supplier, synthetic_code='2201')
        payer = get_or_create_analytic_for_payer(
            self.tenant,
            ExpensePayer.all_objects.create(tenant=self.tenant, name='Vlasnik'),
        )
        self.assertIsNone(supplier.vehicle_id)
        self.assertEqual(supplier.counterparty_type, 'supplier')
        self.assertIsNone(payer.vehicle_id)
        self.assertEqual(payer.counterparty_type, 'payer')

    def test_analytic_subject_must_be_exactly_one_kind(self):
        synthetic = ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code='6600')
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnalyticAccount.all_objects.create(
                tenant=self.tenant,
                synthetic_account=synthetic,
                account_code='6600-MIX',
                account_name='mix',
                counterparty_type='supplier',
                vehicle=self.stock,
                partner=self.supplier,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnalyticAccount.all_objects.create(
                tenant=self.tenant,
                synthetic_account=synthetic,
                account_code='6600-NONE',
                account_name='none',
                counterparty_type='',
            )

    def _fixed_asset(self):
        accounts = {
            code: ChartOfAccounts.all_objects.get(tenant=self.tenant, account_code=code)
            for code in ('0373', '0393')
        }
        postable = ChartOfAccounts.all_objects.filter(tenant=self.tenant, is_postable=True)
        purchase = JournalEntry.all_objects.create(
            tenant=self.tenant,
            entry_number='202610-FA',
            entry_date=date(2026, 10, 1),
            description='FA',
            status='posted',
            created_by=self.user,
        )
        return FixedAsset.all_objects.create(
            tenant=self.tenant,
            name='Mercedes',
            vin='WDC2479121W000001',
            status=FixedAssetStatus.IN_PREPARATION,
            origin=FixedAssetOrigin.PURCHASE,
            acquisition_cost=Decimal('100.00'),
            purchase_date=date(2026, 10, 1),
            depreciation_method=DepreciationMethod.LINEAR,
            construction_account=accounts['0373'],
            asset_account=postable.filter(account_code__startswith='03').first(),
            accumulated_depreciation_account=accounts['0393'],
            depreciation_expense_account=postable.filter(account_code__startswith='43').first(),
            purchase_journal_entry=purchase,
        )

    def test_inventory_vehicle_cannot_link_fixed_asset(self):
        asset = self._fixed_asset()
        with self.assertRaises(ValidationError):
            link_vehicle_to_fixed_asset(self.stock, asset)
        self.stock.fixed_asset = asset
        with self.assertRaises(ValidationError):
            self.stock.clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vehicle.all_objects.filter(pk=self.stock.pk).update(fixed_asset=asset)
        self.stock.refresh_from_db()
        self.assertIsNone(self.stock.fixed_asset_id)

    def test_capitalization_plan_refuses_inventory_vehicle_expense(self):
        row = _inspect_expense(self.tenant, self._expense().pk)
        self.assertTrue(any('zaliha' in blocker for blocker in row['blockers']))
