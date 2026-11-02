"""Deterministic expense posting suggestions (not AI).

Partner default and last approved category are heuristics with a ``source`` +
``reason``. Numeric confidence belongs to a future model in domains/finance/ai/.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from expenses.models import Expense, ExpenseAccountSource, ExpenseCategory
from partners.models import Partner

from domains.finance.services.account_resolver import (
    ResolvedExpenseAccount,
    account_is_usable,
    load_postable_account,
    load_tenant_category,
    resolve_expense_account,
)

FALLBACK_CATEGORY_NAME = 'Ostalo'


@dataclass(frozen=True)
class CategorySuggestion:
    category: ExpenseCategory
    source: str
    reason: str


@dataclass(frozen=True)
class ExpensePostingSuggestion:
    category_id: int
    account_id: int | None
    source: str
    reason: str


def get_or_create_fallback_category(tenant) -> ExpenseCategory:
    category, _ = ExpenseCategory.all_objects.get_or_create(
        tenant=tenant,
        name=FALLBACK_CATEGORY_NAME,
        defaults={'description': 'Ostali troškovi'},
    )
    return category


def suggest_expense_category(
    *,
    tenant,
    partner: Partner | None = None,
    selected_category: ExpenseCategory | None = None,
) -> CategorySuggestion:
    """Priority: explicit selection → partner default → last approved → Ostalo."""
    if selected_category is not None:
        if selected_category.tenant_id != tenant.id:
            raise ValidationError({'category': 'Vrsta troška ne pripada istom tenantu.'})
        return CategorySuggestion(
            category=selected_category,
            source=ExpenseAccountSource.CATEGORY_DEFAULT,
            reason='Odabrana vrsta troška.',
        )

    if partner is not None:
        if partner.tenant_id != tenant.id:
            raise ValidationError({'partner': 'Partner ne pripada istom tenantu.'})
        default_category = partner.default_expense_category
        if (
            default_category is not None
            and default_category.tenant_id == tenant.id
            and default_category.is_active
        ):
            return CategorySuggestion(
                category=default_category,
                source=ExpenseAccountSource.PARTNER_DEFAULT,
                reason='Zadana vrsta troška ovog partnera.',
            )

        history = _last_approved_category(tenant, partner)
        if history is not None:
            return CategorySuggestion(
                category=history,
                source=ExpenseAccountSource.PARTNER_HISTORY,
                reason='Predloženo prema zadnjoj odobrenoj fakturi ovog partnera.',
            )

    fallback = get_or_create_fallback_category(tenant)
    return CategorySuggestion(
        category=fallback,
        source='fallback',
        reason='Nema boljeg prijedloga; korištena je vrsta Ostalo.',
    )


def suggest_expense_posting(
    *,
    tenant,
    partner: Partner | None = None,
    selected_category: ExpenseCategory | None = None,
    expense: Expense | None = None,
    posting_rule=None,
) -> ExpensePostingSuggestion:
    if expense is not None and selected_category is None:
        selected_category = expense.category
        partner = partner or expense.supplier

    category_suggestion = suggest_expense_category(
        tenant=tenant,
        partner=partner,
        selected_category=selected_category,
    )

    account_id = None
    source = category_suggestion.source
    reason = category_suggestion.reason

    if expense is not None and posting_rule is not None:
        resolved: ResolvedExpenseAccount = resolve_expense_account(expense, posting_rule)
        account_id = resolved.account.pk
        source = resolved.source
        if resolved.source == ExpenseAccountSource.MANUAL_OVERRIDE:
            reason = 'Ručno odabrano rashodno konto.'
        elif resolved.source == ExpenseAccountSource.POSTING_RULE_FALLBACK:
            reason = resolved.warning or 'Korišteno je konto iz pravila knjiženja.'
    elif category_suggestion.category.default_account_id:
        account = category_suggestion.category.default_account
        if account_is_usable(account, tenant):
            account_id = account.pk
        else:
            source = ExpenseAccountSource.POSTING_RULE_FALLBACK
            reason = 'Vrsta troška nema valjano zadano konto; korišteno je pravilo knjiženja.'

    return ExpensePostingSuggestion(
        category_id=category_suggestion.category.pk,
        account_id=account_id,
        source=source,
        reason=reason,
    )


def remember_expense_category_for_partner(*, partner: Partner, category: ExpenseCategory) -> None:
    """Persist selected expense *kind* only — never a manual account override."""
    if partner.tenant_id != category.tenant_id:
        raise ValidationError({
            'default_expense_category': 'Vrsta troška ne pripada istom tenantu.',
        })
    if not category.is_active:
        raise ValidationError({
            'default_expense_category': 'Vrsta troška nije aktivna.',
        })
    partner.default_expense_category = category
    partner.save(update_fields=['default_expense_category', 'updated_at'])


def resolve_confirm_posting_inputs(
    *,
    tenant,
    partner: Partner,
    category_id: int | None = None,
    expense_account_id: int | None = None,
    remember_category_for_partner: bool = False,
) -> tuple[ExpenseCategory, object | None, str]:
    """Category + optional override for OCR confirm. Does not build JE lines."""
    if category_id is not None:
        category = load_tenant_category(tenant, category_id)
        source = ExpenseAccountSource.CATEGORY_DEFAULT
    else:
        suggestion = suggest_expense_category(tenant=tenant, partner=partner)
        category = suggestion.category
        if suggestion.source == ExpenseAccountSource.PARTNER_DEFAULT:
            source = ExpenseAccountSource.PARTNER_DEFAULT
        elif suggestion.source == ExpenseAccountSource.PARTNER_HISTORY:
            source = ExpenseAccountSource.PARTNER_HISTORY
        elif category.default_account_id:
            source = ExpenseAccountSource.CATEGORY_DEFAULT
        else:
            source = ExpenseAccountSource.POSTING_RULE_FALLBACK

    account = None
    if expense_account_id is not None:
        account = load_postable_account(tenant, expense_account_id)
        source = ExpenseAccountSource.MANUAL_OVERRIDE

    if remember_category_for_partner:
        remember_expense_category_for_partner(partner=partner, category=category)

    return category, account, source


def _last_approved_category(tenant, partner: Partner) -> ExpenseCategory | None:
    expense = (
        Expense.all_objects.filter(
            tenant=tenant,
            supplier_id=partner.pk,
            status__in=('approved', 'paid'),
            category__is_active=True,
        )
        .select_related('category')
        .order_by('-pk')
        .first()
    )
    if expense is None:
        return None
    category = expense.category
    if category.tenant_id != tenant.id:
        return None
    return category
