"""Auto-knjiženje iz poslovnih dokumenata."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from accounting.models import ChartOfAccounts, FiscalPeriod, JournalEntry, PostingRule
from accounting.services.analytics import (
    get_or_create_analytic_for_partner,
    get_or_create_analytic_for_payer,
)
from domains.finance.services.subledger import (
    get_subledger_item_for_source,
    handle_journal_entry_reversal,
    sync_subledger_for_document_posting,
    sync_subledger_for_invoice_payment,
)


DEFAULT_POSTING_RULES = [
    {
        'name': 'Izdani račun — kupci usluga / prihod',
        'document_type': 'invoice_issued',
        'debit_account_code': '1201',
        'credit_account_code': '7510',
        'amount_field': 'subtotal',
        'priority': 10,
        'use_analytic': True,
    },
    {
        'name': 'Izdani račun — obveza PDV 25%',
        'document_type': 'invoice_issued',
        'debit_account_code': '1201',
        'credit_account_code': '24001',
        'amount_field': 'tax_amount',
        'priority': 20,
        'use_analytic': True,
        'condition': {'min_tax': '0.01'},
    },
    {
        'name': 'Naplata računa — banka / kupci',
        'document_type': 'invoice_paid',
        'debit_account_code': '1000',
        'credit_account_code': '1201',
        'amount_field': 'total_amount',
        'priority': 10,
        'use_analytic': True,
    },
    {
        'name': 'Odobren trošak — rashod / dobavljač',
        'document_type': 'expense_approved',
        'debit_account_code': '4120',
        'credit_account_code': '2201',
        'amount_field': 'net_amount',
        'priority': 10,
        'use_analytic': True,
        'condition': {'posting_profile': ['opex']},
    },
    {
        'name': 'Odobren trošak — pretporez',
        'document_type': 'expense_approved',
        'debit_account_code': '1400',
        'credit_account_code': '2201',
        'amount_field': 'tax_amount',
        'priority': 20,
        'use_analytic': True,
        'condition': {'min_tax': '0.01'},
    },
    {
        'name': 'Odobren trošak — EU usluge RC bez pretporeza',
        'document_type': 'expense_approved',
        'debit_account_code': '4120',
        'credit_account_code': '24032',
        'amount_field': 'eu_rc_vat',
        'priority': 30,
        'use_analytic': False,
        'condition': {
            'posting_profile': ['opex'],
            'eu_service_reverse_charge': True,
            'input_vat_deductible': False,
        },
    },
    {
        'name': 'Odobren trošak — EU usluge RC pretporez',
        'document_type': 'expense_approved',
        'debit_account_code': '14032',
        'credit_account_code': '24032',
        'amount_field': 'eu_rc_vat',
        'priority': 31,
        'use_analytic': False,
        'condition': {
            'posting_profile': ['opex'],
            'eu_service_reverse_charge': True,
            'input_vat_deductible': True,
        },
    },
    {
        'name': 'Odobren trošak — nabava imovine / dobavljač',
        'document_type': 'expense_approved',
        'debit_account_code': '0373',
        'credit_account_code': '2201',
        'amount_field': 'net_amount',
        'priority': 10,
        'use_analytic': True,
        'condition': {'posting_profile': ['asset_purchase']},
    },
    {
        'name': 'Plaćen trošak — dobavljač / platitelj (privatno)',
        'document_type': 'expense_paid',
        'debit_account_code': '2201',
        'credit_account_code': '2309',
        'amount_field': 'total_amount',
        'priority': 10,
        'use_analytic': True,
        'condition': {'settlement_method': ['private_card', 'private_cash']},
    },
    {
        'name': 'Plaćen trošak — dobavljač / poslovni račun',
        'document_type': 'expense_paid',
        'debit_account_code': '2201',
        'credit_account_code': '1000',
        'amount_field': 'total_amount',
        'priority': 20,
        'use_analytic': True,
        'condition': {'settlement_method': ['business_account']},
    },
    {
        'name': 'Plaćen trošak — dobavljač / blagajna',
        'document_type': 'expense_paid',
        'debit_account_code': '2201',
        'credit_account_code': '1020',
        'amount_field': 'total_amount',
        'priority': 30,
        'use_analytic': True,
        'condition': {'settlement_method': ['company_cash']},
    },
    {
        'name': 'Službeni dokument — PPMV nabava vozila / obveza',
        'document_type': 'official_document_posted',
        'debit_account_code': '0373',
        'credit_account_code': '2201',
        'amount_field': 'amount',
        'priority': 10,
        'use_analytic': True,
        'condition': {'posting_profile': ['ppmv_vehicle_acquisition']},
    },
    {
        'name': 'Službeni dokument — upravna pristojba / obveza',
        'document_type': 'official_document_posted',
        'debit_account_code': '4120',
        'credit_account_code': '2201',
        'amount_field': 'amount',
        'priority': 20,
        'use_analytic': True,
        'condition': {'posting_profile': ['administrative_fee']},
    },
    {
        'name': 'Službeni dokument — FZOEU naknada vozila / obveza',
        'document_type': 'official_document_posted',
        'debit_account_code': '0373',
        'credit_account_code': '2201',
        'amount_field': 'amount',
        'priority': 30,
        'use_analytic': True,
        'condition': {'posting_profile': ['fzoeu_vehicle_acquisition_fee']},
    },
    {
        'name': 'Službeni dokument — FZOEU naknada guma / obveza',
        'document_type': 'official_document_posted',
        'debit_account_code': '0373',
        'credit_account_code': '2201',
        'amount_field': 'amount',
        'priority': 40,
        'use_analytic': True,
        'condition': {'posting_profile': ['fzoeu_tire_acquisition_fee']},
    },
]


OFFICIAL_DOCUMENT_POSTED = 'official_document_posted'


class PostingEventSourceMismatch(ValueError):
    """document_type does not match the source model (ADR-0029)."""


def _assert_posting_event_source(source, document_type: str) -> None:
    from accounting.models import OfficialDocument
    from expenses.models import Expense
    from invoices.models import Invoice

    if document_type == OFFICIAL_DOCUMENT_POSTED:
        if not isinstance(source, OfficialDocument):
            raise PostingEventSourceMismatch(
                f'{document_type} prihvaća samo OfficialDocument, dobiven {type(source).__name__}.'
            )
        return
    if document_type.startswith('expense'):
        if isinstance(source, OfficialDocument) or not isinstance(source, Expense):
            raise PostingEventSourceMismatch(
                f'{document_type} prihvaća samo Expense, dobiven {type(source).__name__}.'
            )
        return
    if document_type.startswith('invoice'):
        if isinstance(source, OfficialDocument) or not isinstance(source, Invoice):
            raise PostingEventSourceMismatch(
                f'{document_type} prihvaća samo Invoice, dobiven {type(source).__name__}.'
            )
        return


def _source_posting_profile_code(source) -> str | None:
    raw = getattr(source, 'posting_profile', None)
    if raw is None:
        return None
    code = getattr(raw, 'code', None)
    if code:
        return str(code)
    return str(raw)


def ensure_default_posting_rules(tenant) -> int:
    """Seed missing canonical PostingRule rows only.

    Idempotent create-only: if a rule with the same tenant/document_type/name
    already exists (one or many), it is left untouched. Returns the number of
    newly created rules.
    """
    created = 0
    for rule in DEFAULT_POSTING_RULES:
        exists = PostingRule.all_objects.filter(
            tenant=tenant,
            document_type=rule['document_type'],
            name=rule['name'],
        ).exists()
        if exists:
            continue
        PostingRule.all_objects.create(
            tenant=tenant,
            document_type=rule['document_type'],
            name=rule['name'],
            debit_account_code=rule['debit_account_code'],
            credit_account_code=rule['credit_account_code'],
            amount_field=rule['amount_field'],
            priority=rule['priority'],
            use_analytic=rule.get('use_analytic', False),
            condition=rule.get('condition', {}),
            is_active=True,
        )
        created += 1

    for rule in PostingRule.all_objects.filter(tenant=tenant, document_type='expense_paid', is_active=True):
        if 'settlement_method' not in (rule.condition or {}):
            rule.is_active = False
            rule.save(update_fields=['is_active'])

    ensure_default_official_document_posting_profiles(tenant)
    return created


DEFAULT_OFFICIAL_DOCUMENT_POSTING_PROFILES = [
    {
        'code': 'ppmv_vehicle_acquisition',
        'name': 'PPMV – nabava vozila',
        'economic_effect': 'capitalize',
        'allowed_kinds': ['tax_decision'],
        'requires_fixed_asset': True,
    },
    {
        'code': 'administrative_fee',
        'name': 'Upravna pristojba',
        'economic_effect': 'expense',
        'allowed_kinds': ['other'],
        'requires_fixed_asset': False,
    },
    {
        'code': 'fzoeu_vehicle_acquisition_fee',
        'name': 'FZOEU – naknada za vozilo (nabava)',
        'economic_effect': 'capitalize',
        'allowed_kinds': ['tax_decision'],
        'requires_fixed_asset': True,
    },
    {
        'code': 'fzoeu_tire_acquisition_fee',
        'name': 'FZOEU – naknada za gume (nabava)',
        'economic_effect': 'capitalize',
        'allowed_kinds': ['tax_decision'],
        'requires_fixed_asset': True,
    },
]


def ensure_default_official_document_posting_profiles(tenant) -> int:
    from accounting.models import OfficialDocumentPostingProfile

    created = 0
    for row in DEFAULT_OFFICIAL_DOCUMENT_POSTING_PROFILES:
        exists = OfficialDocumentPostingProfile.all_objects.filter(
            tenant=tenant,
            code=row['code'],
        ).exists()
        if exists:
            continue
        OfficialDocumentPostingProfile.all_objects.create(tenant=tenant, **row)
        created += 1
    return created


@dataclass(frozen=True)
class PostingPlanLine:
    debit_account: ChartOfAccounts
    credit_account: ChartOfAccounts
    amount: Decimal
    description: str
    debit_analytic: object | None
    credit_analytic: object | None
    amount_field: str
    debit_cost_center: object | None = None
    credit_cost_center: object | None = None


@dataclass(frozen=True)
class DocumentPostingPlan:
    document_type: str
    lines: tuple[PostingPlanLine, ...]
    warnings: tuple[str, ...]
    expense_account: ChartOfAccounts | None = None
    account_source: str | None = None


def _active_posting_rules(tenant, document_type: str):
    rules = PostingRule.all_objects.filter(
        tenant=tenant,
        document_type=document_type,
        is_active=True,
    ).order_by('priority')
    if not rules.exists():
        ensure_default_posting_rules(tenant)
        rules = PostingRule.all_objects.filter(
            tenant=tenant,
            document_type=document_type,
            is_active=True,
        ).order_by('priority')
    return rules


def build_document_posting_plan(tenant, source, document_type: str) -> DocumentPostingPlan | None:
    """Build the GL plan without persisting JournalEntry.

    Preview and ``post_document`` must call this same function.
    """
    from domains.finance.services.account_resolver import (
        classify_expense_line_allocation,
        is_expense_cost_amount_rule,
        resolve_expense_account,
    )
    from domains.finance.services.cost_center_resolver import apply_cost_center, resolve_cost_center

    _assert_posting_event_source(source, document_type)
    rules = _active_posting_rules(tenant, document_type)
    lines: list[PostingPlanLine] = []
    warnings: list[str] = []
    expense_account = None
    account_source = None
    expense_account_resolution = None
    line_allocation = None
    if document_type == 'expense_approved':
        line_allocation = classify_expense_line_allocation(source)
        if line_allocation.warning:
            warnings.append(line_allocation.warning)

    for rule in rules:
        amount = _get_amount(source, rule.amount_field)
        if amount <= Decimal('0'):
            continue
        if not _rule_matches(rule, amount, source):
            continue

        debit_code, credit_code, debit_analytic, credit_analytic = _resolve_debit_credit_codes(
            rule, source, tenant, document_type,
        )

        if is_expense_cost_amount_rule(document_type, rule):
            if expense_account_resolution is None:
                expense_account_resolution = resolve_expense_account(source, rule)
                if expense_account_resolution.warning:
                    warnings.append(expense_account_resolution.warning)
            expense_account = expense_account_resolution.account
            account_source = expense_account_resolution.source
            credit_account = resolve_account(tenant, credit_code)
            resolved_cc = resolve_cost_center(source)
            if line_allocation.kind == 'split' and rule.amount_field == 'net_amount':
                for item in line_allocation.lines:
                    if Decimal(item.net_amount) <= Decimal('0'):
                        continue
                    debit_account = item.posting_account
                    description = rule.name
                    if item.description:
                        description = f'{rule.name} — {item.description}'
                    lines.append(
                        PostingPlanLine(
                            debit_account=debit_account,
                            credit_account=credit_account,
                            amount=Decimal(item.net_amount),
                            description=description,
                            debit_analytic=debit_analytic,
                            credit_analytic=credit_analytic,
                            amount_field=rule.amount_field,
                            debit_cost_center=apply_cost_center(debit_account, resolved_cc),
                            credit_cost_center=apply_cost_center(credit_account, resolved_cc),
                        )
                    )
                continue
            debit_account = expense_account_resolution.account
        else:
            debit_account = resolve_account(tenant, debit_code)
            credit_account = resolve_account(tenant, credit_code)
            resolved_cc = resolve_cost_center(source)
            if (
                line_allocation is not None
                and line_allocation.kind == 'split'
                and rule.amount_field == 'tax_amount'
            ):
                for item in line_allocation.lines:
                    vat = Decimal(item.vat_amount)
                    if vat <= Decimal('0'):
                        continue
                    description = rule.name
                    if item.description:
                        description = f'{rule.name} — {item.description}'
                    lines.append(
                        PostingPlanLine(
                            debit_account=debit_account,
                            credit_account=credit_account,
                            amount=vat,
                            description=description,
                            debit_analytic=debit_analytic,
                            credit_analytic=credit_analytic,
                            amount_field=rule.amount_field,
                            debit_cost_center=apply_cost_center(debit_account, resolved_cc),
                            credit_cost_center=apply_cost_center(credit_account, resolved_cc),
                        )
                    )
                continue

        lines.append(
            PostingPlanLine(
                debit_account=debit_account,
                credit_account=credit_account,
                amount=amount,
                description=rule.name,
                debit_analytic=debit_analytic,
                credit_analytic=credit_analytic,
                amount_field=rule.amount_field,
                debit_cost_center=apply_cost_center(debit_account, resolved_cc),
                credit_cost_center=apply_cost_center(credit_account, resolved_cc),
            )
        )

    if not lines:
        return None
    return DocumentPostingPlan(
        document_type=document_type,
        lines=tuple(lines),
        warnings=tuple(warnings),
        expense_account=expense_account,
        account_source=account_source,
    )


def get_or_create_fiscal_period(tenant, entry_date) -> FiscalPeriod | None:
    if entry_date is None:
        return None
    period, _ = FiscalPeriod.all_objects.get_or_create(
        tenant=tenant,
        year=entry_date.year,
        month=entry_date.month,
    )
    return period


def resolve_account(tenant, code: str) -> ChartOfAccounts:
    account = ChartOfAccounts.all_objects.filter(tenant=tenant, account_code=code).first()
    if not account:
        account = ChartOfAccounts.all_objects.filter(tenant=tenant, rrif_code=code).first()
    if not account:
        raise ChartOfAccounts.DoesNotExist(f"Konto {code} ne postoji.")
    return account


def _expense_net_amount(source) -> Decimal:
    if hasattr(source, 'amount') and not hasattr(source, 'subtotal'):
        tax = Decimal(getattr(source, 'tax_amount', 0) or 0)
        return Decimal(source.amount) - tax
    subtotal = getattr(source, 'subtotal', None)
    if subtotal is not None:
        return Decimal(subtotal)
    total = Decimal(getattr(source, 'total_amount', 0) or getattr(source, 'amount', 0) or 0)
    tax = Decimal(getattr(source, 'tax_amount', 0) or 0)
    return total - tax


def _get_amount(source, field: str) -> Decimal:
    if field == 'net_amount':
        return _expense_net_amount(source)
    if field == 'eu_rc_vat':
        net = _expense_net_amount(source)
        if net <= Decimal('0'):
            return Decimal('0.00')
        from accounting.services.tax_forms.pdv.mapping import rc_vat_from_base

        return rc_vat_from_base(net, Decimal('25.00'))
    if field == 'total_amount' and hasattr(source, 'amount') and not hasattr(source, 'total_amount'):
        return Decimal(source.amount)
    value = getattr(source, field, Decimal('0')) or Decimal('0')
    return Decimal(value)


def _rule_matches(rule: PostingRule, amount: Decimal, source=None) -> bool:
    condition = rule.condition or {}
    min_tax = condition.get('min_tax')
    if min_tax is not None and amount < Decimal(str(min_tax)):
        return False

    settlement_methods = condition.get('settlement_method')
    if settlement_methods is not None:
        settlement_method = getattr(source, 'settlement_method', '') or ''
        allowed = settlement_methods if isinstance(settlement_methods, list) else [settlement_methods]
        if settlement_method not in allowed:
            return False

    posting_profiles = condition.get('posting_profile')
    if posting_profiles is not None:
        from accounting.models import OfficialDocument
        from expenses.models import ExpensePostingProfile

        if isinstance(source, OfficialDocument):
            source_profile = _source_posting_profile_code(source)
        else:
            source_profile = getattr(source, 'posting_profile', None) or ExpensePostingProfile.OPEX
        allowed_profiles = (
            posting_profiles if isinstance(posting_profiles, list) else [posting_profiles]
        )
        if source_profile not in allowed_profiles:
            return False
    elif (
        rule.document_type == 'expense_approved'
        and rule.amount_field == 'net_amount'
        and source is not None
        and hasattr(source, 'posting_profile')
    ):
        # Unscoped net rules (typically 4120) stay opex-only so they do not
        # also fire for asset_purchase alongside the explicit 0373 net rule.
        # Tax / pretporez rules (amount_field=tax_amount) stay profile-agnostic.
        from expenses.models import ExpensePostingProfile
        source_profile = getattr(source, 'posting_profile', None) or ExpensePostingProfile.OPEX
        if source_profile != ExpensePostingProfile.OPEX:
            return False

    if condition.get('eu_service_reverse_charge'):
        if source is None or not hasattr(source, 'supplier'):
            return False
        from accounting.services.tax_forms.pdv.mapping import (
            is_eu_goods_acquisition,
            is_eu_supplier,
        )

        supplier = getattr(source, 'supplier', None)
        tax = Decimal(getattr(source, 'tax_amount', 0) or 0)
        net = _expense_net_amount(source)
        if not is_eu_supplier(supplier) or tax != Decimal('0'):
            return False
        if is_eu_goods_acquisition(
            supplier,
            vat_amount=tax,
            base_amount=net,
            description=getattr(source, 'description', '') or '',
        ):
            return False
        required_deductible = condition.get('input_vat_deductible')
        if required_deductible is not None:
            if bool(required_deductible) != _source_input_vat_deductible(source):
                return False

    return True


def _source_input_vat_deductible(source) -> bool:
    from settings.models import CompanySettings

    tenant_id = getattr(source, 'tenant_id', None)
    if tenant_id is None:
        return False
    settings = CompanySettings.all_objects.filter(tenant_id=tenant_id).first()
    return bool(settings and settings.input_vat_deductible)


def _next_entry_number(tenant, entry_date) -> str:
    prefix = entry_date.strftime('%Y%m')
    candidates = JournalEntry.all_objects.filter(
        tenant=tenant,
        entry_number__startswith=f'{prefix}-',
    ).values_list('entry_number', flat=True)
    seq = 0
    for entry_number in candidates:
        suffix = entry_number.rsplit('-', 1)[-1]
        if suffix.isdigit():
            seq = max(seq, int(suffix))
    return f"{prefix}-{seq + 1:04d}"


def _resolve_debit_credit_codes(
    rule: PostingRule,
    source,
    tenant,
    document_type: str,
) -> tuple[str, str, object | None, object | None]:
    debit_code = rule.debit_account_code
    credit_code = rule.credit_account_code
    debit_analytic = None
    credit_analytic = None

    if not rule.use_analytic:
        return debit_code, credit_code, None, None

    if document_type.startswith('invoice'):
        partner = getattr(source, 'company_to', None)
        if partner:
            analytic = get_or_create_analytic_for_partner(tenant, partner)
            code = analytic.account_code
            if debit_code in ('1201', '1200'):
                debit_code = code
                debit_analytic = analytic
            if credit_code in ('1201', '1200'):
                credit_code = code
                credit_analytic = analytic

    elif document_type.startswith('expense') or document_type == OFFICIAL_DOCUMENT_POSTED:
        partner = getattr(source, 'supplier', None) or getattr(source, 'issuer', None)
        if partner:
            analytic = get_or_create_analytic_for_partner(tenant, partner, synthetic_code='2201')
            code = analytic.account_code
            if debit_code in ('2201', '2200'):
                debit_code = code
                debit_analytic = analytic
            if credit_code in ('2201', '2200'):
                credit_code = code
                credit_analytic = analytic

        paid_by = getattr(source, 'paid_by', None)
        if paid_by and credit_code == '2309':
            analytic = get_or_create_analytic_for_payer(tenant, paid_by)
            credit_code = analytic.account_code
            credit_analytic = analytic

    return debit_code, credit_code, debit_analytic, credit_analytic


def _document_reference(source) -> str:
    for attr in ('invoice_number', 'expense_number', 'payment_number', 'receipt_number', 'document_number'):
        value = getattr(source, attr, None)
        if value:
            return str(value)
    return ''


def invoice_payment_marker(payment) -> str:
    return f'[invoice_paid:payment-{payment.pk}]'


@transaction.atomic
def post_invoice_payment(
    tenant,
    invoice,
    payment,
    user,
    *,
    auto_post: bool = True,
) -> JournalEntry | None:
    """Knjiži jednu djelomičnu uplatu računa (banka / kupci)."""
    from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

    lock_open_vat_period_for_source_mutation(tenant, invoice.issue_date)
    if payment.tenant_id != tenant.id:
        raise ValueError('Uplata ne pripada tenantu.')
    if payment.related_invoice_id != invoice.pk:
        raise ValueError('Uplata nije povezana s računom.')

    amount = Decimal(payment.amount or 0)
    if amount <= Decimal('0'):
        return None

    partner = invoice.company_to
    if partner is None:
        return None

    marker = invoice_payment_marker(payment)
    payment_ct = ContentType.objects.get_for_model(payment)
    existing = (
        JournalEntry.all_objects.filter(
            tenant=tenant,
            source_content_type=payment_ct,
            source_object_id=payment.pk,
            description__startswith=marker,
            status__in=['draft', 'posted'],
        )
        .order_by('id')
        .first()
    )
    if existing is not None:
        # Heal missing allocation when payment JE already exists (legacy / partial sync).
        sync_subledger_for_invoice_payment(tenant, invoice, payment, existing)
        return existing

    entry_date = payment.payment_date
    analytic = get_or_create_analytic_for_partner(tenant, partner)
    bank_account = resolve_account(tenant, '1000')
    customer_account = analytic.chart_account

    invoice_ref = _document_reference(invoice)
    desc = f'{marker} {payment} — račun {invoice_ref}'
    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, entry_date),
        entry_date=entry_date,
        status='draft',
        description=desc,
        reference=_document_reference(payment),
        is_auto=True,
        source_content_type=payment_ct,
        source_object_id=payment.pk,
        fiscal_period=get_or_create_fiscal_period(tenant, entry_date),
        created_by=user,
    )

    from accounting.services.journal_lines import persist_journal_entry_line

    line_desc = 'Naplata računa — banka / kupci'
    persist_journal_entry_line(
        journal_entry=entry,
        account=bank_account,
        description=line_desc,
        debit_amount=amount,
        credit_amount=Decimal('0'),
    )
    persist_journal_entry_line(
        journal_entry=entry,
        account=customer_account,
        analytic_account=analytic,
        description=line_desc,
        debit_amount=Decimal('0'),
        credit_amount=amount,
    )

    if auto_post:
        entry.post(user)

    sync_subledger_for_invoice_payment(tenant, invoice, payment, entry)

    return entry


@transaction.atomic
def post_document(
    tenant,
    source,
    document_type: str,
    user,
    *,
    auto_post: bool = True,
    entry_date=None,
    description: str | None = None,
) -> JournalEntry | None:
    from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

    _assert_posting_event_source(source, document_type)
    ct = ContentType.objects.get_for_model(source)
    marker = f"[{document_type}]"
    existing = (
        JournalEntry.all_objects.filter(
            tenant=tenant,
            source_content_type=ct,
            source_object_id=source.pk,
            description__startswith=marker,
            status__in=['draft', 'posted'],
        )
        .order_by('id')
        .first()
    )
    if existing is not None:
        if document_type == OFFICIAL_DOCUMENT_POSTED:
            item = get_subledger_item_for_source(tenant, source)
            if item is None:
                raise ValueError(
                    'official_document_posted JE bez SubledgerItem nije uspješan posting; heal je zabranjen.'
                )
            return existing
        # Heal missing subledger when posting JE already exists (legacy / partial sync).
        sync_subledger_for_document_posting(tenant, source, document_type, existing)
        return existing

    if entry_date is None:
        entry_date = (
            getattr(source, 'issue_date', None)
            or getattr(source, 'expense_date', None)
            or getattr(source, 'payment_date', None)
            or timezone.now().date()
        )
    if isinstance(entry_date, str):
        from datetime import datetime
        entry_date = datetime.strptime(entry_date[:10], '%Y-%m-%d').date()

    lock_open_vat_period_for_source_mutation(tenant, entry_date)

    plan = build_document_posting_plan(tenant, source, document_type)
    if plan is None:
        return None

    if description:
        desc = description
    else:
        desc = f"{marker} {source}"
        receipt = (getattr(source, 'receipt_number', '') or '').strip()
        if receipt:
            desc = f'{desc} Račun: {receipt}'
    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, entry_date),
        entry_date=entry_date,
        status='draft',
        description=desc,
        reference=_document_reference(source),
        is_auto=True,
        source_content_type=ct,
        source_object_id=source.pk,
        fiscal_period=get_or_create_fiscal_period(tenant, entry_date),
        created_by=user,
    )

    from accounting.services.journal_lines import persist_journal_entry_line

    for line in plan.lines:
        persist_journal_entry_line(
            journal_entry=entry,
            account=line.debit_account,
            analytic_account=line.debit_analytic,
            cost_center=line.debit_cost_center,
            description=line.description,
            debit_amount=line.amount,
            credit_amount=Decimal('0'),
        )
        persist_journal_entry_line(
            journal_entry=entry,
            account=line.credit_account,
            analytic_account=line.credit_analytic,
            cost_center=line.credit_cost_center,
            description=line.description,
            debit_amount=Decimal('0'),
            credit_amount=line.amount,
        )

    if auto_post:
        entry.post(user)

    sync_subledger_for_document_posting(tenant, source, document_type, entry)

    return entry
