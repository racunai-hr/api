"""Expense category suggestion + account resolver + shared posting plan."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import ChartOfAccounts, JournalEntryLine, PostingRule
from accounting.services.chart import provision_tenant_chart
from accounting.services.posting import (
    build_document_posting_plan,
    ensure_default_posting_rules,
    post_document,
)
from accounting.services.rrif_import import import_rrif_chart
from domains.finance.services.account_resolver import (
    ExpenseAccountResolutionError,
    is_expense_cost_amount_rule,
    resolve_expense_account,
)
from domains.finance.services.posting_suggestions import (
    remember_expense_category_for_partner,
    suggest_expense_category,
)
from expenses.models import (
    Expense,
    ExpenseAccountSource,
    ExpenseCategory,
    ExpensePostingProfile,
    ExpenseSource,
)
from partners.models import Partner
from tenants.models import Tenant


class ExpenseAccountResolverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_rrif_chart(clear=True)
        cls.tenant = Tenant.objects.create(slug='exp-acc', name='Exp Acc')
        cls.other = Tenant.objects.create(slug='exp-acc-other', name='Other')
        provision_tenant_chart(cls.tenant)
        provision_tenant_chart(cls.other)
        ensure_default_posting_rules(cls.tenant)
        User = get_user_model()
        cls.user = User.objects.create_user(username='exp-acc-user', password='test')
        cls.supplier = Partner.all_objects.create(
            tenant=cls.tenant,
            name='A1 Hrvatska',
            partner_type='supplier',
            status='active',
            address='Ulica 1',
            city='Zagreb',
            postal_code='10000',
        )
        cls.ostalo = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ostalo')
        cls.telekom = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Telekomunikacije',
            default_account=cls._account('4100'),
        )
        cls.gorivo = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Gorivo',
            default_account=cls._account('4010'),
        )
        cls.other_category = ExpenseCategory.all_objects.create(
            tenant=cls.other,
            name='Telekomunikacije',
        )

    @classmethod
    def _account(cls, code: str, *, tenant=None) -> ChartOfAccounts:
        tenant = tenant or cls.tenant
        return ChartOfAccounts.all_objects.get(tenant=tenant, account_code=code)

    def _opex_net_rule(self) -> PostingRule:
        return PostingRule.all_objects.get(
            tenant=self.tenant,
            document_type='expense_approved',
            amount_field='net_amount',
            name='Odobren trošak — rashod / dobavljač',
        )

    def _asset_net_rule(self) -> PostingRule:
        return PostingRule.all_objects.get(
            tenant=self.tenant,
            document_type='expense_approved',
            amount_field='net_amount',
            name='Odobren trošak — nabava imovine / dobavljač',
        )

    def _expense(self, **overrides) -> Expense:
        defaults = {
            'tenant': self.tenant,
            'expense_number': f'T-EA-{Expense.all_objects.filter(tenant=self.tenant).count() + 1}',
            'source': ExpenseSource.MANUAL,
            'status': 'draft',
            'category': self.ostalo,
            'supplier': self.supplier,
            'amount': Decimal('125.00'),
            'tax_amount': Decimal('25.00'),
            'currency': 'EUR',
            'expense_date': date(2026, 8, 1),
            'description': 'Resolver test',
            'created_by': self.user,
            'vat_procedure': 'standard',
        }
        defaults.update(overrides)
        return Expense.all_objects.create(**defaults)

    def test_explicit_account_beats_category_default(self):
        expense = self._expense(
            category=self.telekom,
            expense_account=self._account('4123'),
            expense_account_source=ExpenseAccountSource.MANUAL_OVERRIDE,
        )
        resolved = resolve_expense_account(expense, self._opex_net_rule())
        self.assertEqual(resolved.account.account_code, '4123')
        self.assertEqual(resolved.source, ExpenseAccountSource.MANUAL_OVERRIDE)

    def test_explicit_category_beats_partner_default(self):
        self.supplier.default_expense_category = self.gorivo
        self.supplier.save(update_fields=['default_expense_category', 'updated_at'])
        suggestion = suggest_expense_category(
            tenant=self.tenant,
            partner=self.supplier,
            selected_category=self.telekom,
        )
        self.assertEqual(suggestion.category.pk, self.telekom.pk)
        self.assertEqual(suggestion.source, ExpenseAccountSource.CATEGORY_DEFAULT)

    def test_partner_default_beats_history(self):
        older = self._expense(category=self.telekom, status='approved')
        self.assertIsNotNone(older.pk)
        self.supplier.default_expense_category = self.gorivo
        self.supplier.save(update_fields=['default_expense_category', 'updated_at'])
        suggestion = suggest_expense_category(tenant=self.tenant, partner=self.supplier)
        self.assertEqual(suggestion.category.pk, self.gorivo.pk)
        self.assertEqual(suggestion.source, ExpenseAccountSource.PARTNER_DEFAULT)

    def test_history_beats_ostalo(self):
        self._expense(category=self.telekom, status='approved')
        suggestion = suggest_expense_category(tenant=self.tenant, partner=self.supplier)
        self.assertEqual(suggestion.category.pk, self.telekom.pk)
        self.assertEqual(suggestion.source, ExpenseAccountSource.PARTNER_HISTORY)
        self.assertIn('zadnjoj odobrenoj', suggestion.reason)

    def test_fallback_ostalo_when_no_signal(self):
        suggestion = suggest_expense_category(tenant=self.tenant, partner=self.supplier)
        self.assertEqual(suggestion.category.name, 'Ostalo')
        self.assertEqual(suggestion.source, 'fallback')

    def test_category_without_account_uses_posting_rule(self):
        expense = self._expense(category=self.ostalo)
        resolved = resolve_expense_account(expense, self._opex_net_rule())
        self.assertEqual(resolved.account.account_code, '4120')
        self.assertEqual(resolved.source, ExpenseAccountSource.POSTING_RULE_FALLBACK)

    def test_4120_code_is_not_a_sentinel_on_net_rule(self):
        rule = self._opex_net_rule()
        rule.debit_account_code = '4110'
        rule.save(update_fields=['debit_account_code'])
        expense = self._expense(category=self.ostalo)
        resolved = resolve_expense_account(expense, rule)
        self.assertEqual(resolved.account.account_code, '4110')
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        net = [line for line in plan.lines if line.amount_field == 'net_amount']
        self.assertEqual(len(net), 1)
        self.assertEqual(net[0].debit_account.account_code, '4110')

    def test_4120_on_tax_line_is_not_replaced_by_category(self):
        tax_rule = PostingRule.all_objects.get(
            tenant=self.tenant,
            document_type='expense_approved',
            amount_field='tax_amount',
        )
        tax_rule.debit_account_code = '4120'
        tax_rule.save(update_fields=['debit_account_code'])
        expense = self._expense(category=self.telekom)
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        net = [line for line in plan.lines if line.amount_field == 'net_amount']
        tax = [line for line in plan.lines if line.amount_field == 'tax_amount']
        self.assertEqual(net[0].debit_account.account_code, '4100')
        self.assertEqual(tax[0].debit_account.account_code, '4120')

    def test_nondeductible_eu_rc_is_resolved_as_expense_cost(self):
        nondeductible_rule = PostingRule.all_objects.get(
            tenant=self.tenant,
            document_type='expense_approved',
            amount_field='eu_rc_vat',
            condition__input_vat_deductible=False,
        )
        deductible_rule = PostingRule.all_objects.get(
            tenant=self.tenant,
            document_type='expense_approved',
            amount_field='eu_rc_vat',
            condition__input_vat_deductible=True,
        )

        self.assertTrue(is_expense_cost_amount_rule('expense_approved', nondeductible_rule))
        self.assertFalse(is_expense_cost_amount_rule('expense_approved', deductible_rule))
        resolved = resolve_expense_account(
            self._expense(category=self.telekom),
            nondeductible_rule,
        )
        self.assertEqual(resolved.account.account_code, '4100')

    def test_asset_purchase_keeps_rule_account_not_category(self):
        expense = self._expense(
            category=self.telekom,
            posting_profile=ExpensePostingProfile.ASSET_PURCHASE,
        )
        resolved = resolve_expense_account(expense, self._asset_net_rule())
        self.assertEqual(resolved.account.account_code, '0373')
        self.assertEqual(resolved.source, ExpenseAccountSource.POSTING_RULE_FALLBACK)
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        net = [line for line in plan.lines if line.amount_field == 'net_amount']
        self.assertEqual(net[0].debit_account.account_code, '0373')

    def test_cross_tenant_category_rejected(self):
        with self.assertRaises(ValidationError):
            suggest_expense_category(
                tenant=self.tenant,
                partner=self.supplier,
                selected_category=self.other_category,
            )

    def test_non_postable_override_rejected(self):
        synthetic = ChartOfAccounts.all_objects.get(
            tenant=self.tenant, account_code='41', is_postable=False,
        )
        expense = self._expense(
            expense_account=synthetic,
            expense_account_source=ExpenseAccountSource.MANUAL_OVERRIDE,
        )
        with self.assertRaises(ExpenseAccountResolutionError):
            resolve_expense_account(expense, self._opex_net_rule())

    def test_approved_expense_accounting_inputs_locked(self):
        expense = self._expense(status='approved', category=self.ostalo)
        expense.category = self.telekom
        with self.assertRaises(ValidationError):
            expense.save()
        expense.refresh_from_db()
        self.assertEqual(expense.category_id, self.ostalo.pk)

    def test_remember_for_partner_saves_only_category(self):
        remember_expense_category_for_partner(partner=self.supplier, category=self.telekom)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.default_expense_category_id, self.telekom.pk)
        self.assertFalse(hasattr(self.supplier, 'expense_account'))

    def test_preview_plan_matches_posted_journal(self):
        expense = self._expense(
            category=self.telekom,
            expense_account=self._account('4123'),
            expense_account_source=ExpenseAccountSource.MANUAL_OVERRIDE,
        )
        plan = build_document_posting_plan(self.tenant, expense, 'expense_approved')
        entry = post_document(self.tenant, expense, 'expense_approved', self.user)
        posted = list(
            JournalEntryLine.objects.filter(journal_entry=entry).order_by('id').values_list(
                'account__account_code', 'debit_amount', 'credit_amount',
            )
        )
        planned = []
        for line in plan.lines:
            planned.append((line.debit_account.account_code, line.amount, Decimal('0')))
            planned.append((line.credit_account.account_code, Decimal('0'), line.amount))
        self.assertEqual(posted, planned)
        self.assertEqual(plan.account_source, ExpenseAccountSource.MANUAL_OVERRIDE)

    def test_vat_procedure_and_input_vat_line_unchanged(self):
        expense = self._expense(category=self.telekom, vat_procedure='standard')
        entry = post_document(self.tenant, expense, 'expense_approved', self.user)
        expense.refresh_from_db()
        self.assertEqual(expense.vat_procedure, 'standard')
        tax_debits = list(
            JournalEntryLine.objects.filter(
                journal_entry=entry,
                debit_amount__gt=0,
                description__icontains='pretporez',
            ).values_list('account__account_code', 'debit_amount')
        )
        self.assertEqual(tax_debits, [('1400', Decimal('25.00'))])
        net_debits = list(
            JournalEntryLine.objects.filter(
                journal_entry=entry,
                debit_amount__gt=0,
                description__icontains='rashod',
            ).values_list('account__account_code', 'debit_amount')
        )
        self.assertEqual(net_debits, [('4100', Decimal('100.00'))])
