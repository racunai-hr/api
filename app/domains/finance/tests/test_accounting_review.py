"""Universal accounting review: vehicle context beats supplier memory, and the 50% split is 20/25/5."""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounting.models import ChartOfAccounts, Vehicle, VehicleAccountingRole
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import build_document_posting_plan, ensure_default_posting_rules
from accounting.services.rrif_import import import_rrif_chart
from domains.banking.write.expense_close import CloseError, close_expense_from_bank
from domains.finance.ai import accounting_classify
from domains.finance.ai.accounting_classify import build_prompt
from domains.finance.services import accounting_review as review_module
from domains.finance.services.accounting_review import (
    bind_review_choice,
    review_expense,
)
from domains.finance.services.supplier_prepayment_api import (
    PrepaymentApiError,
    apply_prepayments_for_source,
)
from accounting.services.foreign_vat import eur_split
from accounting.services.fx import FxRate
from domains.tax.classification.contracts import (
    Direction,
    EventKind,
    OriginTaxOwner,
    PartnerProvenance,
    PartnerSnapshot,
    TaxDocumentInput,
    TaxRelevance,
)
from domains.tax.classification.engine import classify
from domains.tax.classification.contracts import Outcome
from expenses.models import (
    AccountingClassification,
    Expense,
    ExpenseCategory,
    ExpenseLine,
    ExpensePostingProfile,
    SupplierItemProfileMemory,
    SupplierLinePostingPattern,
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
    debit = {}
    credit = {}
    for line in plan.lines:
        debit[line.debit_account.account_code] = debit.get(line.debit_account.account_code, Decimal('0')) + line.amount
        credit[line.credit_account.account_code] = credit.get(line.credit_account.account_code, Decimal('0')) + line.amount
    return debit, credit


@override_settings(ACCOUNTING_CLASSIFY_PROVIDER='fake', ACCOUNTING_CLASSIFY_FAKE=FUEL)
class AccountingReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='acct-review', name='Accounting Review')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='acct-review', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='owner')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljac token',
            partner_type='supplier',
            status='active',
        )
        cls.stock = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='Zaliha',
            registration_plate='ZG1001AA',
            accounting_role=VehicleAccountingRole.INVENTORY_FOR_RESALE,
        )
        cls.passenger = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='Sluzbeno',
            registration_plate='ZG2002BB',
            accounting_role=VehicleAccountingRole.COMPANY_PASSENGER,
        )
        cls.full = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='Puni odbitak',
            registration_plate='ZG3003CC',
            accounting_role=VehicleAccountingRole.FULL_VAT_BUSINESS_USE,
        )
        cls.blank = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='Bez namjene',
            registration_plate='ZG4004DD',
        )

    def setUp(self):
        AccountingClassification.all_objects.filter(tenant=self.tenant).delete()

    def _expense(self, **overrides):
        number = overrides.pop('expense_number', f'T-{Expense.all_objects.count() + 1}')
        defaults = {
            'tenant': self.tenant,
            'expense_number': number,
            'status': 'draft',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('50.00'),
            'tax_amount': Decimal('10.00'),
            'expense_date': date(2026, 10, 1),
            'description': 'ED CP',
            'created_by': self.user,
            'receipt_number': '409334',
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def test_inventory_vehicle_posts_to_vehicle_inventory_analytic(self):
        expense = self._expense(vehicle=self.stock)
        result = review_expense(self.tenant, expense, None, self.user)
        self.assertEqual(result['posting_profile'], 'vehicle_inventory_cost')
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['line_accounts'], [])
        bind_review_choice(expense, result, None)
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        debit, credit = _sums(plan)
        inventory = f'6600-V{self.stock.pk:05d}'
        self.assertEqual(debit[inventory], Decimal('40.00'))
        self.assertEqual(debit['1400'], Decimal('10.00'))
        self.assertNotIn('6514', debit)
        self.assertEqual(sum(amount for code, amount in credit.items() if code == '2201' or code.startswith('2201-')), Decimal('50.00'))

    def test_company_passenger_split_is_half_net_plus_nondeductible_vat(self):
        expense = self._expense(vehicle=self.passenger)
        result = review_expense(self.tenant, expense, None, self.user)
        self.assertEqual(result['posting_profile'], 'company_passenger_vehicle')
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['line_accounts'], [])
        expense.posting_profile = ExpensePostingProfile.COMPANY_PASSENGER_VEHICLE
        expense.save()
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        debit, credit = _sums(plan)
        self.assertEqual(debit['4075'], Decimal('20.00'))
        self.assertEqual(debit['4076'], Decimal('25.00'))
        self.assertEqual(debit['1400'], Decimal('5.00'))
        self.assertEqual(sum(amount for code, amount in credit.items() if code == '2201' or code.startswith('2201-')), Decimal('50.00'))
        self.assertNotIn('6514', debit)
        self.assertNotIn(Decimal('22.50'), debit.values())

    def test_full_vat_without_debit_rule_has_no_preview(self):
        expense = self._expense(vehicle=self.full)
        result = review_expense(self.tenant, expense, None, self.user)
        self.assertEqual(result['status'], 'needs_information')
        self.assertIsNone(result['preview'])
        self.assertNotIn('4077', str(result))

    def test_fuel_without_vehicle_asks_and_skips_preview(self):
        expense = self._expense()
        result = review_expense(self.tenant, expense, None, self.user)
        self.assertEqual(result['status'], 'needs_information')
        self.assertIsNone(result['preview'])
        self.assertEqual(result['line_accounts'], [])
        self.assertTrue(any(item['type'] == 'vehicle_select' for item in result['questions']))

    def test_vehicle_without_role_asks_and_remember_is_explicit(self):
        expense = self._expense(vehicle=self.blank)
        result = review_expense(self.tenant, expense, None, self.user)
        self.assertEqual(result['status'], 'needs_information')
        self.assertTrue(any(item['key'] == 'vehicle_accounting_role' for item in result['questions']))
        bind_review_choice(
            expense,
            {'posting_profile': 'company_passenger_vehicle', 'vehicle': {'id': self.blank.pk}},
            {'vehicle_accounting_role': 'company_passenger', 'remember_vehicle_role': False},
        )
        self.blank.refresh_from_db()
        self.assertEqual(self.blank.accounting_role, '')
        bind_review_choice(
            expense,
            {'posting_profile': 'company_passenger_vehicle', 'vehicle': {'id': self.blank.pk}},
            {'vehicle_accounting_role': 'company_passenger', 'remember_vehicle_role': True},
        )
        self.blank.refresh_from_db()
        self.assertEqual(self.blank.accounting_role, 'company_passenger')

    def test_vehicle_role_beats_remembered_supplier_item(self):
        from domains.finance.services.posting_memory import line_signature, normalize_line_text

        SupplierItemProfileMemory.all_objects.create(
            tenant=self.tenant,
            supplier=self.supplier,
            line_signature=line_signature([normalize_line_text('ED CP')]),
            posting_profile='goods_inward_cost',
        )
        expense = self._expense(vehicle=self.passenger, description='ED CP')
        result = review_expense(self.tenant, expense, None, self.user)
        self.assertEqual(result['posting_profile'], 'company_passenger_vehicle')
        self.assertEqual(result['decision_source'], 'vehicle')
        self.assertEqual(result['line_accounts'], [])

    @override_settings(
        ACCOUNTING_CLASSIFY_PROVIDER='fake',
        ACCOUNTING_CLASSIFY_FAKE={
            'purchase_nature': 'fuel',
            'suggested_profile': 'goods_inward_cost',
            'required_context': ['vehicle'],
            'reason': 'nesigurno',
            'confidence': 'low',
        },
    )
    def test_low_confidence_is_not_resolved(self):
        result = review_expense(self.tenant, self._expense(), None, self.user)
        self.assertNotEqual(result['status'], 'resolved')
        self.assertIsNone(result['preview'])

    @override_settings(
        ACCOUNTING_CLASSIFY_PROVIDER='fake',
        ACCOUNTING_CLASSIFY_FAKE={
            'purchase_nature': 'rocket',
            'suggested_profile': 'goods_inward_cost',
            'required_context': [],
            'reason': 'nepoznato',
            'confidence': 'high',
        },
    )
    def test_unknown_enum_is_not_resolved(self):
        result = review_expense(self.tenant, self._expense(), None, self.user)
        self.assertNotEqual(result['status'], 'resolved')
        self.assertIsNone(result['purchase_nature'])

    def test_close_and_prepayment_reject_missing_information(self):
        expense = self._expense()
        with self.assertRaises(CloseError) as close_error:
            close_expense_from_bank(
                tenant=self.tenant,
                user=self.user,
                expense_id=expense.pk,
                bank_transaction_id=1,
                lines=[],
                remember=False,
                idempotency_key='review-close',
            )
        self.assertEqual(close_error.exception.status_code, 422)
        with self.assertRaises(PrepaymentApiError) as prepay_error:
            apply_prepayments_for_source(
                tenant=self.tenant,
                user=self.user,
                source_type='expense',
                source_id=expense.pk,
                applications=[{'prepayment_id': 1, 'amount': '50.00'}],
                lines=[],
                remember=False,
                idempotency_key='review-prepay',
            )
        self.assertEqual(prepay_error.exception.status_code, 422)

    def test_prompt_and_service_do_not_name_a_supplier_or_accounts(self):
        text = inspect.getsource(review_module) + inspect.getsource(accounting_classify)
        self.assertNotIn('INA', text)
        for code in ('6514', '4075', '4076', '4077', '1400', '2201', '22.50', '22,50'):
            self.assertNotIn(code, text)
        prompt = build_prompt({
            'number': '1',
            'document_date': '2026-10-01',
            'net_amount': '40.00',
            'tax_amount': '10.00',
            'gross_amount': '50.00',
            'currency': 'EUR',
            'tax_category': '',
            'lines': [{'description': 'ED CP', 'amount': '50.00'}],
            'history_profiles': [],
            'remembered_profile': '',
            'vehicle_role': '',
            'supplier_name': 'Dobavljac token',
        })
        self.assertNotIn('Dobavljac token', prompt)
        self.assertNotIn('Uloga vozila', prompt)
        self.assertIn('ED CP', prompt)

    def _postable_cost_account(self, exclude: str = ''):
        rows = ChartOfAccounts.all_objects.filter(
            tenant=self.tenant,
            is_active=True,
            is_postable=True,
            account_code__startswith='4',
        ).order_by('account_code')
        if exclude:
            rows = rows.exclude(account_code=exclude)
        return rows.first()

    def test_goods_inward_manual_line_account_is_posted(self):
        from domains.banking.write.expense_close import _apply_accounting_review

        other = self._postable_cost_account('6514')
        expense = self._expense(vehicle=self.stock)
        ExpenseLine.all_objects.create(
            tenant=self.tenant,
            expense=expense,
            position=1,
            description='ED CP',
            net_amount=Decimal('40.00'),
            vat_amount=Decimal('10.00'),
            gross_amount=Decimal('50.00'),
        )
        _apply_accounting_review(
            self.tenant,
            self.user,
            expense,
            {'posting_profile': 'goods_inward_cost'},
            [{'position': 1, 'account_id': other.pk}],
        )
        self.assertEqual(expense.lines.get().posting_account_id, other.pk)
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        debit, _credit = _sums(plan)
        self.assertEqual(debit[other.account_code], Decimal('40.00'))
        self.assertNotIn('6514', debit)

    def test_passenger_manual_line_account_is_rejected(self):
        expense = self._expense(vehicle=self.passenger)
        with self.assertRaises(CloseError) as caught:
            close_expense_from_bank(
                tenant=self.tenant,
                user=self.user,
                expense_id=expense.pk,
                bank_transaction_id=1,
                lines=[{'position': 1, 'account_id': self._postable_cost_account().pk}],
                remember=False,
                idempotency_key='passenger-manual',
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn('osobno', caught.exception.detail)

    @override_settings(
        ACCOUNTING_CLASSIFY_PROVIDER='fake',
        ACCOUNTING_CLASSIFY_FAKE={
            'purchase_nature': 'service',
            'suggested_profile': 'opex',
            'required_context': [],
            'reason': 'Usluga',
            'confidence': 'high',
        },
    )
    def test_opex_uses_remembered_accounts_and_category_default(self):
        account = self._postable_cost_account()
        from domains.finance.services.posting_memory import line_signature, normalize_line_text

        SupplierLinePostingPattern.all_objects.create(
            tenant=self.tenant,
            supplier=self.supplier,
            line_signature=line_signature([normalize_line_text('Zapamcena stavka')]),
            accounts=[{
                'position': 1,
                'description': normalize_line_text('Zapamcena stavka'),
                'account_id': account.pk,
            }],
        )
        remembered = review_expense(
            self.tenant,
            self._expense(description='Zapamcena stavka'),
            None,
            self.user,
        )
        self.assertEqual(remembered['posting_profile'], 'opex')
        self.assertEqual(remembered['line_accounts'][0]['source'], 'remembered')
        self.assertEqual(remembered['line_accounts'][0]['account_id'], account.pk)

        empty = review_expense(self.tenant, self._expense(description='Bez mape'), None, self.user)
        self.assertEqual(empty['line_accounts'], [])

        category = ExpenseCategory.all_objects.create(
            tenant=self.tenant,
            name='Sa kontom',
            default_account=account,
        )
        filled = review_expense(
            self.tenant,
            self._expense(description='Vrsta troska', category=category),
            None,
            self.user,
        )
        self.assertEqual(filled['line_accounts'][0]['source'], 'category')
        self.assertEqual(filled['line_accounts'][0]['account_id'], account.pk)

    def test_classification_cache_ignores_answers_until_version_or_facts_change(self):
        expense = self._expense(description='Cache stavka', receipt_number='CACHE-1')
        with patch(
            'domains.finance.ai.accounting_classify._invoke',
            return_value=dict(FUEL),
        ) as invoke:
            review_expense(self.tenant, expense, None, self.user)
            review_expense(
                self.tenant,
                expense,
                {'vehicle_id': self.stock.pk},
                self.user,
            )
            self.assertEqual(invoke.call_count, 1)
            with override_settings(OPENAI_ACCOUNT_SUGGESTION_MODEL='other-model'):
                review_expense(self.tenant, expense, None, self.user)
            self.assertEqual(invoke.call_count, 2)
            expense.receipt_number = 'CACHE-2'
            expense.save(update_fields=['receipt_number', 'updated_at'])
            review_expense(self.tenant, expense, None, self.user)
            self.assertEqual(invoke.call_count, 3)
            with patch('domains.finance.ai.accounting_classify.CLASSIFIER_PROMPT_VERSION', 2):
                review_expense(self.tenant, expense, None, self.user)
            self.assertEqual(invoke.call_count, 4)


class PassengerVatGateTests(TestCase):
    def test_company_passenger_is_review_before_domestic_box(self):
        document = TaxDocumentInput(
            tenant_id=1,
            source_kind='expense',
            source_document_id=1,
            source_line_id=None,
            lifecycle_status='approved',
            event_kind=EventKind.ORIGINAL,
            direction=Direction.INPUT,
            document_date=date(2026, 10, 1),
            supply_date=None,
            partner=None,
            base_amount=Decimal('40.00'),
            vat_rate=Decimal('25.00'),
            vat_amount=Decimal('10.00'),
            currency='EUR',
            jurisdiction='',
            customer_type='B2B',
            supply_kind='unknown',
            declared_procedure='standard',
            originates_from=None,
            origin_tax_effects=(),
            origin_effects_ambiguous=False,
            tax_relevance=TaxRelevance.TAX_RELEVANT,
            origin_tax_owner=OriginTaxOwner.NONE,
            has_linked_journal_entry=False,
            account_code=None,
            debit_amount=Decimal('0.00'),
            credit_amount=Decimal('0.00'),
            description='',
            period_year=2026,
            period_month=10,
            input_vat_deductible=True,
            input_hash='x',
            posting_profile='company_passenger_vehicle',
        )
        result = classify(document)
        self.assertEqual(result.outcome, Outcome.REVIEW_REQUIRED)
        self.assertEqual(result.rule_code, 'EXP_VEHICLE_LIMITED_VAT_REVIEW')
        self.assertEqual(result.rows, ())


def _credit(rows, field='credit'):
    return sum(
        Decimal(row[field])
        for row in rows
        if row['account_code'] == '2201' or str(row['account_code']).startswith('2201-')
    )


def _debit(rows, code):
    return sum(Decimal(row['debit']) for row in rows if row['account_code'] == code)


@override_settings(ACCOUNTING_CLASSIFY_PROVIDER='fake', ACCOUNTING_CLASSIFY_FAKE=FUEL)
class ForeignVatTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='foreign-vat', name='Foreign VAT')
        provision_tenant_chart(cls.tenant)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='foreign-vat', password='test')
        TenantMembership.objects.create(user=cls.user, tenant=cls.tenant, role='owner')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='Dobavljac token',
            partner_type='supplier',
            status='active',
        )
        cls.stock = Vehicle.all_objects.create(
            tenant=cls.tenant,
            name='Zaliha',
            registration_plate='ZG1001AA',
            accounting_role=VehicleAccountingRole.INVENTORY_FOR_RESALE,
        )
        cls.inv = f'6600-V{cls.stock.pk:05d}'

    def setUp(self):
        AccountingClassification.all_objects.filter(tenant=self.tenant).delete()

    def _expense(self, **overrides):
        number = overrides.pop('expense_number', f'FX-{Expense.all_objects.count() + 1}')
        defaults = {
            'tenant': self.tenant,
            'expense_number': number,
            'status': 'draft',
            'category': self.category,
            'supplier': self.supplier,
            'amount': Decimal('50.00'),
            'tax_amount': Decimal('10.00'),
            'expense_date': date(2026, 10, 1),
            'description': 'ED CP',
            'created_by': self.user,
            'receipt_number': '409334',
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def _dkk(self, **overrides):
        defaults = {
            'vehicle': self.stock,
            'posting_profile': ExpensePostingProfile.VEHICLE_INVENTORY_COST,
            'amount': Decimal('731.11'),
            'tax_amount': Decimal('0.00'),
            'currency': 'DKK',
            'foreign_net_amount': Decimal('584.89'),
            'foreign_vat_amount': Decimal('146.22'),
            'foreign_gross_amount': Decimal('731.11'),
            'foreign_vat_country': 'DK',
            'foreign_vat_rate': Decimal('25.00'),
            'expense_date': date(2026, 10, 23),
            'description': 'Diesel B7',
        }
        defaults.update(overrides)
        return self._expense(**defaults)

    def test_net_is_gross_minus_rounded_vat(self):
        split = eur_split(Decimal('100'), Decimal('20'), Decimal('3'), 'refund_receivable')
        self.assertEqual(split['gross'], Decimal('33.33'))
        self.assertEqual(split['refund'], Decimal('6.67'))
        self.assertEqual(split['cost'], Decimal('26.66'))
        self.assertEqual(split['cost'] + split['refund'], split['gross'])
        danish = eur_split(Decimal('731.11'), Decimal('146.22'), Decimal('7.4747'), 'refund_receivable')
        self.assertEqual(danish['gross'], Decimal('97.81'))
        self.assertEqual(danish['refund'], Decimal('19.56'))
        self.assertEqual(danish['cost'], Decimal('78.25'))
        bundled = eur_split(Decimal('731.11'), Decimal('146.22'), Decimal('7.4747'), 'expense')
        self.assertEqual(bundled['cost'], bundled['gross'])
        self.assertEqual(bundled['refund'], Decimal('0.00'))

    def test_unanswered_foreign_vat_asks_without_a_preview(self):
        expense = self._dkk()
        result = review_expense(self.tenant, expense, None, self.user)
        self.assertEqual(result['status'], 'needs_information')
        self.assertIsNone(result['preview'])
        self.assertEqual(result['foreign_vat']['treatment'], '')
        self.assertEqual(result['foreign_vat']['amount'], '146.22')
        question = next(row for row in result['questions'] if row['key'] == 'foreign_vat_treatment')
        self.assertEqual(
            [option['value'] for option in question['options']],
            ['expense', 'refund_receivable'],
        )
        self.assertIn('Danski PDV: 146,22 DKK', question['label'])
        self.assertNotIn('97,81', question['label'])
        self.assertNotIn('19,56', question['label'])
        self.assertTrue(any(row['key'] == 'fx_rate' for row in result['questions']))
        expense.tax_amount = Decimal('146.22')
        expense.save(update_fields=['tax_amount'])
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        debit = {} if plan is None else _sums(plan)[0]
        self.assertNotIn('1400', debit)

    def test_missing_rate_blocks_preview_until_it_is_entered(self):
        expense = self._dkk()
        result = review_expense(
            self.tenant,
            expense,
            {'foreign_vat_treatment': 'expense'},
            self.user,
        )
        self.assertEqual(result['status'], 'needs_information')
        self.assertIsNone(result['preview'])
        self.assertEqual([row['key'] for row in result['questions']], ['fx_rate'])

    def test_expense_treatment_posts_eur_gross_from_the_same_snapshot(self):
        expense = self._dkk(tax_amount=Decimal('146.22'))
        rate = FxRate(Decimal('7.4747'), 'manual', date(2026, 10, 23))
        with patch(
            'domains.finance.services.accounting_review.resolve_fx_rate',
            return_value=rate,
        ):
            result = review_expense(
                self.tenant,
                expense,
                {'foreign_vat_treatment': 'expense'},
                self.user,
            )
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['foreign_vat']['fx_rate'], '7.4747')
        self.assertEqual(result['foreign_vat']['fx_rate_source'], 'manual')
        self.assertEqual(result['foreign_vat']['treatment'], 'expense')
        self.assertEqual(_debit(result['preview'], self.inv), Decimal('97.81'))
        self.assertEqual(_debit(result['preview'], '1274'), Decimal('0'))
        self.assertEqual(_debit(result['preview'], '1400'), Decimal('0'))
        self.assertEqual(_credit(result['preview']), Decimal('97.81'))
        bind_review_choice(expense, result, {'foreign_vat_treatment': 'expense'})
        expense.refresh_from_db()
        self.assertEqual(expense.tax_amount, Decimal('0.00'))
        self.assertEqual(expense.currency, 'DKK')
        self.assertEqual(expense.foreign_gross_amount, Decimal('731.11'))
        self.assertEqual(expense.foreign_vat_treatment, 'expense')
        self.assertEqual(expense.fx_rate, Decimal('7.4747'))
        self.assertEqual(expense.fx_rate_source, 'manual')
        self.assertEqual(expense.refund_eligibility, 'candidate')
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        debit, credit = _sums(plan)
        self.assertEqual(debit[self.inv], Decimal('97.81'))
        self.assertNotIn('1400', debit)
        self.assertNotIn('1274', debit)
        self.assertEqual(
            sum(amount for code, amount in credit.items() if code == '2201' or code.startswith('2201-')),
            Decimal('97.81'),
        )

    def test_refund_treatment_posts_net_and_foreign_vat_from_the_same_snapshot(self):
        expense = self._dkk()
        rate = FxRate(Decimal('7.4747'), 'manual', date(2026, 10, 23))
        with patch(
            'domains.finance.services.accounting_review.resolve_fx_rate',
            return_value=rate,
        ):
            result = review_expense(
                self.tenant,
                expense,
                {'foreign_vat_treatment': 'refund_receivable'},
                self.user,
            )
        self.assertEqual(result['foreign_vat']['fx_rate'], '7.4747')
        self.assertEqual(_debit(result['preview'], self.inv), Decimal('78.25'))
        self.assertEqual(_debit(result['preview'], '1274'), Decimal('19.56'))
        self.assertEqual(_debit(result['preview'], '1400'), Decimal('0'))
        self.assertEqual(_credit(result['preview']), Decimal('97.81'))
        self.assertEqual(
            _debit(result['preview'], self.inv) + _debit(result['preview'], '1274'),
            _credit(result['preview']),
        )

    def test_currency_alone_does_not_open_the_foreign_vat_gate(self):
        expense = self._expense(
            vehicle=self.stock,
            currency='DKK',
            amount=Decimal('50.00'),
            tax_amount=Decimal('10.00'),
        )
        result = review_expense(self.tenant, expense, None, self.user)
        self.assertIsNone(result['foreign_vat'])
        self.assertFalse(any(row['key'] == 'foreign_vat_treatment' for row in result['questions']))
        self.assertEqual(result['status'], 'resolved')

    def test_eur_supplier_invoice_without_foreign_vat_keeps_the_reverse_charge_path(self):
        partner = PartnerSnapshot(
            name='EU dobavljac',
            country='Danska',
            tax_number='',
            vat_id='DK12345678',
            provenance=PartnerProvenance.CURRENT_PARTNER,
            country_code='DK',
        )
        shared = dict(
            tenant_id=1,
            source_kind='expense',
            source_document_id=1,
            source_line_id=None,
            lifecycle_status='approved',
            event_kind=EventKind.ORIGINAL,
            direction=Direction.INPUT,
            document_date=date(2026, 10, 23),
            supply_date=None,
            partner=partner,
            base_amount=Decimal('100.00'),
            vat_rate=None,
            vat_amount=Decimal('0.00'),
            currency='EUR',
            jurisdiction='',
            customer_type='B2B',
            supply_kind='service',
            declared_procedure='standard',
            originates_from=None,
            origin_tax_effects=(),
            origin_effects_ambiguous=False,
            tax_relevance=TaxRelevance.TAX_RELEVANT,
            origin_tax_owner=OriginTaxOwner.NONE,
            has_linked_journal_entry=False,
            account_code=None,
            debit_amount=Decimal('0.00'),
            credit_amount=Decimal('0.00'),
            description='Usluga',
            period_year=2026,
            period_month=10,
            input_vat_deductible=True,
            input_hash='x',
        )
        foreign = classify(TaxDocumentInput(
            **shared,
            foreign_vat_amount=Decimal('25.00'),
            foreign_vat_country='DK',
        ))
        self.assertEqual(foreign.outcome, Outcome.NOT_TAX_RELEVANT)
        self.assertEqual(foreign.reason, 'foreign_input_vat')
        self.assertEqual(foreign.rule_code, 'EXP_FOREIGN_INPUT_VAT')
        self.assertEqual(foreign.rows, ())
        domestic_path = classify(TaxDocumentInput(**shared))
        self.assertNotEqual(domestic_path.rule_code, 'EXP_FOREIGN_INPUT_VAT')
        self.assertNotEqual(domestic_path.reason, 'foreign_input_vat')
        self.assertTrue(domestic_path.rows)
