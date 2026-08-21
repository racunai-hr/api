"""Public read-model service: list, detail, export (ADR-0020)."""

from __future__ import annotations

from datetime import date

from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum
from django.http import Http404

from accounting.models import Deposit, SubledgerItem
from expenses.models import Expense, ExpenseAttachment
from invoices.models import Invoice

from domains.reporting.documents.assemble import documents_from_keys
from domains.reporting.documents.export import DocumentExportService, ExportLimitExceeded
from domains.reporting.documents.filters import DocumentListFilters, as_of_date
from domains.reporting.documents.query import (
    count_union,
    filtered_deposits,
    filtered_expenses,
    filtered_invoices,
    paginate_union,
    union_rows,
)
from domains.reporting.documents.relations import load_page_relations
from domains.reporting.documents.snapshot import isoformat, read_snapshot


def _keys_from_union_page(page_rows) -> list[tuple[str, int]]:
    return [(row['_direction'], row['_doc_id']) for row in page_rows]


def _kpi(tenant, filters: DocumentListFilters, today: date) -> dict:
    from django.db.models import Count

    from domains.reporting.documents.projection import money

    invoices = filtered_invoices(tenant, filters, today)
    expenses = filtered_expenses(tenant, filters, today)
    deposits = filtered_deposits(tenant, filters, today)
    invoice_ct = ContentType.objects.get_for_model(Invoice)
    expense_ct = ContentType.objects.get_for_model(Expense)
    deposit_ct = ContentType.objects.get_for_model(Deposit)

    inv_count = invoices.count()
    inv_gross = invoices.aggregate(total=Sum('total_amount'))['total']
    dep_count = deposits.count()
    dep_gross = deposits.aggregate(total=Sum('amount'))['total']
    exp_by_currency = list(
        expenses.values('currency').annotate(count=Count('id'), total=Sum('amount'))
    )
    open_ar_inv = SubledgerItem.all_objects.filter(
        tenant=tenant,
        direction='receivable',
        status__in=('open', 'partial'),
        source_content_type=invoice_ct,
        source_object_id__in=invoices.values('pk'),
    ).aggregate(total=Sum('open_amount'))['total']
    open_ar_dep = SubledgerItem.all_objects.filter(
        tenant=tenant,
        direction='receivable',
        status__in=('open', 'partial'),
        source_content_type=deposit_ct,
        source_object_id__in=deposits.values('pk'),
    ).aggregate(total=Sum('open_amount'))['total']
    open_ar = (open_ar_inv or 0) + (open_ar_dep or 0)
    open_ap = SubledgerItem.all_objects.filter(
        tenant=tenant,
        direction='payable',
        status__in=('open', 'partial'),
        source_content_type=expense_ct,
        source_object_id__in=expenses.values('pk'),
    ).aggregate(total=Sum('open_amount'))['total']

    by_currency: dict[str, dict] = {}
    eur = by_currency.setdefault('EUR', {
        'outgoing_count': 0,
        'incoming_count': 0,
        'outgoing_gross': '0.00',
        'incoming_gross': '0.00',
        'open_receivables': '0.00',
        'open_payables': '0.00',
    })
    eur['outgoing_count'] = inv_count + dep_count
    eur['outgoing_gross'] = money((inv_gross or 0) + (dep_gross or 0))
    eur['open_receivables'] = money(open_ar)

    for row in exp_by_currency:
        currency = row['currency'] or 'EUR'
        bucket = by_currency.setdefault(currency, {
            'outgoing_count': 0,
            'incoming_count': 0,
            'outgoing_gross': '0.00',
            'incoming_gross': '0.00',
            'open_receivables': '0.00',
            'open_payables': '0.00',
        })
        bucket['incoming_count'] = row['count']
        bucket['incoming_gross'] = money(row['total'] or 0)
    if 'EUR' in by_currency:
        by_currency['EUR']['open_payables'] = money(open_ap or 0)
    return {'by_currency': by_currency}


def list_documents(tenant, filters: DocumentListFilters) -> dict:
    with read_snapshot() as as_of:
        today = as_of_date(as_of)
        union = union_rows(tenant, filters, today)
        total = count_union(union)
        page_rows = paginate_union(union, filters.page, filters.page_size)
        keys = _keys_from_union_page(page_rows)
        rel = load_page_relations(tenant, keys)
        results = documents_from_keys(tenant, keys, rel, today, detail=False)
        summary = _kpi(tenant, filters, today)
        return {
            'as_of': isoformat(as_of),
            'count': total,
            'page': filters.page,
            'page_size': filters.page_size,
            'results': results,
            'summary': summary,
        }


def get_document_detail(tenant, direction: str, pk: int) -> dict:
    if direction not in ('outgoing', 'incoming', 'deposit'):
        raise Http404()
    with read_snapshot() as as_of:
        today = as_of_date(as_of)
        if direction == 'outgoing':
            model = Invoice
        elif direction == 'incoming':
            model = Expense
        else:
            model = Deposit
        exists = model.all_objects.filter(tenant=tenant, pk=pk).exists()
        if not exists:
            raise Http404()
        keys = [(direction, pk)]
        rel = load_page_relations(tenant, keys)
        rows = documents_from_keys(tenant, keys, rel, today, detail=True)
        if not rows:
            raise Http404()
        payload = rows[0]
        payload['as_of'] = isoformat(as_of)
        return payload


def export_documents(tenant, filters: DocumentListFilters, fmt: str) -> tuple[bytes, str, str]:
    return DocumentExportService(tenant, filters).export(fmt)


def get_expense_attachment(tenant, expense_id: int, attachment_id: int) -> ExpenseAttachment:
    attachment = ExpenseAttachment.all_objects.filter(
        tenant=tenant,
        expense_id=expense_id,
        pk=attachment_id,
    ).select_related('expense').first()
    if attachment is None:
        raise Http404()
    return attachment
