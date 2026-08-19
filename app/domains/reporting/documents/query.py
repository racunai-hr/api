"""Database-level UNION ALL projection for document list (ADR-0020 §10)."""

from __future__ import annotations

from datetime import date

from django.contrib.contenttypes.models import ContentType
from django.db.models import (
    CharField,
    Exists,
    F,
    OuterRef,
    Q,
    Value,
)
from django.db.models.functions import Coalesce

from accounting.models import JournalEntry, SubledgerItem, VATLedgerEntry
from expenses.models import Expense
from fiscal_gateway.models import As4DocumentLink
from integrations.models import IntegrationOutboxMessage
from invoices.models import Invoice

from domains.reporting.documents.filters import DocumentListFilters

_CHAR = CharField()

TAX_ACTIVE_INVOICE = frozenset({'sent', 'paid', 'overdue'})
TAX_ACTIVE_EXPENSE = frozenset({'approved', 'paid'})
OPEN_SUBLEDGER = frozenset({'open', 'partial'})


def _base_invoices(tenant):
    return Invoice.all_objects.filter(tenant=tenant).select_related('company_to')


def _base_expenses(tenant):
    return Expense.all_objects.filter(tenant=tenant).select_related('supplier')


def _apply_common_invoice_filters(qs, filters: DocumentListFilters):
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.search:
        qs = qs.filter(
            Q(invoice_number__icontains=filters.search)
            | Q(company_to__name__icontains=filters.search)
            | Q(company_to__tax_number__icontains=filters.search)
        )
    if filters.year:
        qs = qs.filter(issue_date__year=filters.year)
    if filters.month:
        qs = qs.filter(issue_date__month=filters.month)
    if filters.partner_id:
        qs = qs.filter(company_to_id=filters.partner_id)
    if filters.oib:
        qs = qs.filter(company_to__tax_number=filters.oib)
    if filters.date_from:
        qs = qs.filter(issue_date__gte=filters.date_from)
    if filters.date_to:
        qs = qs.filter(issue_date__lte=filters.date_to)
    if filters.due_from:
        qs = qs.filter(due_date__gte=filters.due_from)
    if filters.due_to:
        qs = qs.filter(due_date__lte=filters.due_to)
    if filters.amount_min is not None:
        qs = qs.filter(total_amount__gte=filters.amount_min)
    if filters.amount_max is not None:
        qs = qs.filter(total_amount__lte=filters.amount_max)
    if filters.currency and filters.currency != 'EUR':
        qs = qs.none()
    return qs


def _apply_common_expense_filters(qs, filters: DocumentListFilters):
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.search:
        qs = qs.filter(
            Q(expense_number__icontains=filters.search)
            | Q(receipt_number__icontains=filters.search)
            | Q(supplier__name__icontains=filters.search)
            | Q(supplier__tax_number__icontains=filters.search)
        )
    if filters.year:
        qs = qs.filter(expense_date__year=filters.year)
    if filters.month:
        qs = qs.filter(expense_date__month=filters.month)
    if filters.partner_id:
        qs = qs.filter(supplier_id=filters.partner_id)
    if filters.oib:
        qs = qs.filter(supplier__tax_number=filters.oib)
    if filters.date_from:
        qs = qs.filter(expense_date__gte=filters.date_from)
    if filters.date_to:
        qs = qs.filter(expense_date__lte=filters.date_to)
    if filters.due_from:
        qs = qs.filter(due_date__gte=filters.due_from)
    if filters.due_to:
        qs = qs.filter(due_date__lte=filters.due_to)
    if filters.amount_min is not None:
        qs = qs.filter(amount__gte=filters.amount_min)
    if filters.amount_max is not None:
        qs = qs.filter(amount__lte=filters.amount_max)
    if filters.currency:
        qs = qs.filter(currency=filters.currency)
    return qs


def _subledger_exists(tenant, model, statuses):
    ct = ContentType.objects.get_for_model(model)
    return SubledgerItem.all_objects.filter(
        tenant=tenant,
        source_content_type=ct,
        source_object_id=OuterRef('pk'),
        status__in=statuses,
    )


def _journal_exists(tenant, model, statuses=('posted', 'draft')):
    ct = ContentType.objects.get_for_model(model)
    return JournalEntry.all_objects.filter(
        tenant=tenant,
        source_content_type=ct,
        source_object_id=OuterRef('pk'),
        status__in=statuses,
    )


def _ledger_exists(tenant, model):
    ct = ContentType.objects.get_for_model(model)
    return VATLedgerEntry.all_objects.filter(
        tenant=tenant,
        source_content_type=ct,
        source_object_id=OuterRef('pk'),
    )


def _as4_rejected_exists(tenant, model):
    ct = ContentType.objects.get_for_model(model)
    return As4DocumentLink.all_objects.filter(
        tenant=tenant,
        content_type=ct,
        object_id=OuterRef('pk'),
        as4_status__in=(As4DocumentLink.STATUS_REJECTED, As4DocumentLink.STATUS_FAILED),
    )


def _apply_invoice_view(qs, tenant, filters: DocumentListFilters, today: date):
    view = filters.view
    if not view:
        return qs
    if view in {
        'incoming_ready_to_pay',
    }:
        return qs.none()
    if view == 'unpaid_outgoing':
        return qs.filter(Exists(_subledger_exists(tenant, Invoice, OPEN_SUBLEDGER)))
    if view == 'overdue_outgoing':
        return qs.filter(
            Exists(_subledger_exists(tenant, Invoice, OPEN_SUBLEDGER)),
            due_date__lt=today,
        )
    if view == 'partially_paid':
        return qs.filter(Exists(_subledger_exists(tenant, Invoice, ('partial',))))
    if view == 'unposted':
        return qs.filter(status__in=('sent', 'paid', 'overdue')).exclude(
            Exists(_journal_exists(tenant, Invoice, ('posted',)))
        )
    if view == 'vat_pending':
        return qs.filter(status__in=TAX_ACTIVE_INVOICE).exclude(
            Exists(_ledger_exists(tenant, Invoice))
        )
    if view == 'vat_mismatch':
        return qs.filter(status__in=TAX_ACTIVE_INVOICE).filter(Exists(_ledger_exists(tenant, Invoice)))
    if view == 'eracun_rejected':
        return qs.filter(Exists(_as4_rejected_exists(tenant, Invoice)))
    if view == 'possible_duplicates':
        dup = Invoice.all_objects.filter(
            tenant=tenant,
            company_to_id=OuterRef('company_to_id'),
            invoice_number=OuterRef('invoice_number'),
            issue_date=OuterRef('issue_date'),
            total_amount=OuterRef('total_amount'),
        ).exclude(pk=OuterRef('pk'))
        return qs.filter(Exists(dup))
    if view == 'bank_unmatched':
        # Only when a bank transfer payment exists and no matched bank tx — applied in SQL via payments.
        from payments.models import Payment
        from banking.models import BankTransaction

        unmatched_pay = Payment.all_objects.filter(
            tenant=tenant,
            related_invoice_id=OuterRef('pk'),
            payment_method='bank_transfer',
        ).exclude(
            Exists(
                BankTransaction.all_objects.filter(
                    matched_payment_id=OuterRef('pk'),
                    match_status='matched',
                )
            )
        )
        return qs.filter(Exists(unmatched_pay))
    if view == 'attention':
        missing_oib = Q(company_to__tax_number='') | Q(company_to__isnull=True)
        overdue = Q(due_date__lt=today) & Exists(_subledger_exists(tenant, Invoice, OPEN_SUBLEDGER))
        paid_open = Q(status='paid') & Exists(_subledger_exists(tenant, Invoice, OPEN_SUBLEDGER))
        unposted = Q(status__in=('sent', 'paid', 'overdue')) & ~Exists(
            _journal_exists(tenant, Invoice, ('posted',))
        )
        vat_pending = Q(status__in=TAX_ACTIVE_INVOICE) & ~Exists(_ledger_exists(tenant, Invoice))
        rejected = Exists(_as4_rejected_exists(tenant, Invoice))
        outbox = Exists(
            IntegrationOutboxMessage.all_objects.filter(
                tenant=tenant,
                invoice_id=OuterRef('pk'),
                status__in=(
                    IntegrationOutboxMessage.STATUS_PENDING,
                    IntegrationOutboxMessage.STATUS_RETRYING,
                ),
            )
        )
        return qs.filter(missing_oib | overdue | paid_open | unposted | vat_pending | rejected | outbox)
    return qs


def _apply_expense_view(qs, tenant, filters: DocumentListFilters, today: date):
    view = filters.view
    if not view:
        return qs
    if view in {'unpaid_outgoing', 'overdue_outgoing', 'eracun_rejected', 'bank_unmatched'}:
        if view == 'eracun_rejected':
            return qs.filter(Exists(_as4_rejected_exists(tenant, Expense)))
        return qs.none()
    if view == 'incoming_ready_to_pay':
        return qs.filter(status='approved').filter(Exists(_subledger_exists(tenant, Expense, OPEN_SUBLEDGER)))
    if view == 'partially_paid':
        return qs.filter(Exists(_subledger_exists(tenant, Expense, ('partial',))))
    if view == 'unposted':
        return qs.filter(status__in=('approved', 'paid')).exclude(
            Exists(_journal_exists(tenant, Expense, ('posted',)))
        )
    if view == 'vat_pending':
        return qs.filter(status__in=TAX_ACTIVE_EXPENSE).exclude(Exists(_ledger_exists(tenant, Expense)))
    if view == 'vat_mismatch':
        return qs.filter(status__in=TAX_ACTIVE_EXPENSE).filter(Exists(_ledger_exists(tenant, Expense)))
    if view == 'possible_duplicates':
        dup = Expense.all_objects.filter(
            tenant=tenant,
            supplier_id=OuterRef('supplier_id'),
            receipt_number=OuterRef('receipt_number'),
            expense_date=OuterRef('expense_date'),
            amount=OuterRef('amount'),
        ).exclude(pk=OuterRef('pk'))
        return qs.filter(Exists(dup))
    if view == 'attention':
        missing_oib = Q(supplier__tax_number='') | Q(supplier__isnull=True)
        overdue = Q(due_date__isnull=False, due_date__lt=today) & Exists(
            _subledger_exists(tenant, Expense, OPEN_SUBLEDGER)
        )
        paid_open = Q(status='paid') & Exists(_subledger_exists(tenant, Expense, OPEN_SUBLEDGER))
        unposted = Q(status__in=('approved', 'paid')) & ~Exists(
            _journal_exists(tenant, Expense, ('posted',))
        )
        vat_pending = Q(status__in=TAX_ACTIVE_EXPENSE) & ~Exists(_ledger_exists(tenant, Expense))
        rejected = Exists(_as4_rejected_exists(tenant, Expense))
        return qs.filter(missing_oib | overdue | paid_open | unposted | vat_pending | rejected)
    return qs


def filtered_invoices(tenant, filters: DocumentListFilters, today: date):
    if filters.direction == 'incoming':
        return _base_invoices(tenant).none()
    qs = _apply_common_invoice_filters(_base_invoices(tenant), filters)
    return _apply_invoice_view(qs, tenant, filters, today)


def filtered_expenses(tenant, filters: DocumentListFilters, today: date):
    if filters.direction == 'outgoing':
        return _base_expenses(tenant).none()
    qs = _apply_common_expense_filters(_base_expenses(tenant), filters)
    return _apply_expense_view(qs, tenant, filters, today)


def _invoice_values(qs):
    return qs.annotate(
        _doc_id=F('id'),
        _direction=Value('outgoing', output_field=_CHAR),
        _document_date=F('issue_date'),
        _due_date=F('due_date'),
        _internal_number=F('invoice_number'),
        _source_number=F('invoice_number'),
        _document_status=F('status'),
        _partner_id=F('company_to_id'),
        _gross=F('total_amount'),
        _net=F('subtotal'),
        _vat_amount=F('tax_amount'),
        _currency=Value('EUR', output_field=_CHAR),
    ).values(
        '_doc_id',
        '_direction',
        '_document_date',
        '_due_date',
        '_internal_number',
        '_source_number',
        '_document_status',
        '_partner_id',
        '_gross',
        '_net',
        '_vat_amount',
        '_currency',
    )


def _expense_values(qs):
    return qs.annotate(
        _doc_id=F('id'),
        _direction=Value('incoming', output_field=_CHAR),
        _document_date=F('expense_date'),
        _due_date=F('due_date'),
        _internal_number=F('expense_number'),
        _source_number=Coalesce(F('receipt_number'), F('expense_number')),
        _document_status=F('status'),
        _partner_id=F('supplier_id'),
        _gross=F('amount'),
        _net=F('amount') - F('tax_amount'),
        _vat_amount=F('tax_amount'),
        _currency=F('currency'),
    ).values(
        '_doc_id',
        '_direction',
        '_document_date',
        '_due_date',
        '_internal_number',
        '_source_number',
        '_document_status',
        '_partner_id',
        '_gross',
        '_net',
        '_vat_amount',
        '_currency',
    )


def union_rows(tenant, filters: DocumentListFilters, today: date):
    inv = _invoice_values(filtered_invoices(tenant, filters, today))
    exp = _expense_values(filtered_expenses(tenant, filters, today))
    combined = inv.union(exp, all=True)
    return combined.order_by('-_document_date', '_direction', '-_doc_id')


def paginate_union(union_qs, page: int, page_size: int):
    offset = (page - 1) * page_size
    rows = list(union_qs[offset:offset + page_size])
    return rows


def count_union(union_qs) -> int:
    return union_qs.count()
