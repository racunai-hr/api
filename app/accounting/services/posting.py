"""Auto-knjiženje iz poslovnih dokumenata."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from accounting.models import ChartOfAccounts, FiscalPeriod, JournalEntry, JournalEntryLine, PostingRule
from accounting.services.analytics import (
    get_or_create_analytic_for_partner,
    get_or_create_analytic_for_payer,
)
from domains.finance.services.subledger import (
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
]


def ensure_default_posting_rules(tenant) -> int:
    created = 0
    for rule in DEFAULT_POSTING_RULES:
        _, was_created = PostingRule.all_objects.update_or_create(
            tenant=tenant,
            document_type=rule['document_type'],
            name=rule['name'],
            defaults={
                'debit_account_code': rule['debit_account_code'],
                'credit_account_code': rule['credit_account_code'],
                'amount_field': rule['amount_field'],
                'priority': rule['priority'],
                'use_analytic': rule.get('use_analytic', False),
                'condition': rule.get('condition', {}),
                'is_active': True,
            },
        )
        if was_created:
            created += 1

    for rule in PostingRule.all_objects.filter(tenant=tenant, document_type='expense_paid', is_active=True):
        if 'settlement_method' not in (rule.condition or {}):
            rule.is_active = False
            rule.save(update_fields=['is_active'])

    return created


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


def _get_amount(source, field: str) -> Decimal:
    if field == 'net_amount':
        if hasattr(source, 'amount') and not hasattr(source, 'subtotal'):
            tax = Decimal(getattr(source, 'tax_amount', 0) or 0)
            return Decimal(source.amount) - tax
        subtotal = getattr(source, 'subtotal', None)
        if subtotal is not None:
            return Decimal(subtotal)
        total = Decimal(getattr(source, 'total_amount', 0) or getattr(source, 'amount', 0) or 0)
        tax = Decimal(getattr(source, 'tax_amount', 0) or 0)
        return total - tax
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

    return True


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

    elif document_type.startswith('expense'):
        supplier = getattr(source, 'supplier', None)
        if supplier:
            analytic = get_or_create_analytic_for_partner(tenant, supplier, synthetic_code='2201')
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
    for attr in ('invoice_number', 'expense_number', 'payment_number', 'receipt_number'):
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
    if JournalEntry.all_objects.filter(
        tenant=tenant,
        source_content_type=payment_ct,
        source_object_id=payment.pk,
        description__startswith=marker,
        status__in=['draft', 'posted'],
    ).exists():
        return None

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

    line_desc = 'Naplata računa — banka / kupci'
    JournalEntryLine.objects.create(
        journal_entry=entry,
        account=bank_account,
        description=line_desc,
        debit_amount=amount,
        credit_amount=Decimal('0'),
    )
    JournalEntryLine.objects.create(
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

    ct = ContentType.objects.get_for_model(source)
    marker = f"[{document_type}]"
    if JournalEntry.all_objects.filter(
        tenant=tenant,
        source_content_type=ct,
        source_object_id=source.pk,
        description__startswith=marker,
        status__in=['draft', 'posted'],
    ).exists():
        return None

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

    lines_data = []

    for rule in rules:
        amount = _get_amount(source, rule.amount_field)
        if amount <= Decimal('0'):
            continue
        if not _rule_matches(rule, amount, source):
            continue

        debit_code, credit_code, debit_analytic, credit_analytic = _resolve_debit_credit_codes(
            rule, source, tenant, document_type,
        )

        if document_type == 'expense_approved' and rule.debit_account_code == '4120':
            category = getattr(source, 'category', None)
            if category and getattr(category, 'default_account_id', None):
                debit_account = category.default_account
            else:
                debit_account = resolve_account(tenant, debit_code)
        else:
            debit_account = resolve_account(tenant, debit_code)

        credit_account = resolve_account(tenant, credit_code)

        lines_data.append({
            'debit_account': debit_account,
            'credit_account': credit_account,
            'amount': amount,
            'description': rule.name,
            'debit_analytic': debit_analytic,
            'credit_analytic': credit_analytic,
        })

    if not lines_data:
        return None

    desc = description or f"{marker} {source}"
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

    for line in lines_data:
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=line['debit_account'],
            analytic_account=line['debit_analytic'],
            description=line['description'],
            debit_amount=line['amount'],
            credit_amount=Decimal('0'),
        )
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=line['credit_account'],
            analytic_account=line['credit_analytic'],
            description=line['description'],
            debit_amount=Decimal('0'),
            credit_amount=line['amount'],
        )

    if auto_post:
        entry.post(user)

    sync_subledger_for_document_posting(tenant, source, document_type, entry)

    return entry
