"""Batch-load related domain records for a page of document IDs."""

from __future__ import annotations

from collections import defaultdict

from django.contrib.contenttypes.models import ContentType

from accounting.models import (
    Deposit,
    JournalEntry,
    JournalEntryLine,
    SubledgerAllocation,
    SubledgerItem,
    SubmissionEvent,
    VATLedgerEntry,
    VATReturn,
)
from banking.models import BankTransaction
from banking.provider_models import PaymentOrder
from expenses.models import Expense, ExpenseAttachment
from fiscal_gateway.models import As4DocumentLink, FiscalSubmissionLog
from integrations.models import IntegrationOutboxMessage
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner, PartnerBankAccount
from payments.models import Payment
from super_integration.models import SuperDocumentLink


def _ct(model):
    return ContentType.objects.get_for_model(model)


def load_page_relations(tenant, keys: list[tuple[str, int]]):
    """keys: list of (direction, id). Returns a dict of batched maps."""
    outgoing_ids = [pk for direction, pk in keys if direction == 'outgoing']
    incoming_ids = [pk for direction, pk in keys if direction == 'incoming']
    deposit_ids = [pk for direction, pk in keys if direction == 'deposit']
    invoice_ct = _ct(Invoice)
    expense_ct = _ct(Expense)
    deposit_ct = _ct(Deposit)

    invoices = {
        inv.pk: inv
        for inv in Invoice.all_objects.filter(tenant=tenant, pk__in=outgoing_ids).select_related(
            'company_to', 'created_by',
        )
    }
    expenses = {
        exp.pk: exp
        for exp in Expense.all_objects.filter(tenant=tenant, pk__in=incoming_ids).select_related(
            'supplier', 'created_by', 'category',
        )
    }
    deposits = {
        dep.pk: dep
        for dep in Deposit.all_objects.filter(tenant=tenant, pk__in=deposit_ids).select_related(
            'partner', 'created_by', 'return_bank_account',
        )
    }
    items_by_invoice = defaultdict(list)
    for item in InvoiceItem.objects.filter(invoice_id__in=outgoing_ids):
        items_by_invoice[item.invoice_id].append(item)

    attachments_by_expense = defaultdict(list)
    for att in ExpenseAttachment.all_objects.filter(tenant=tenant, expense_id__in=incoming_ids):
        attachments_by_expense[att.expense_id].append(att)

    partner_ids = (
        {inv.company_to_id for inv in invoices.values()}
        | {exp.supplier_id for exp in expenses.values()}
        | {dep.partner_id for dep in deposits.values()}
    )
    partners = {
        p.pk: p
        for p in Partner.all_objects.filter(tenant=tenant, pk__in=partner_ids)
    }
    ibans_by_partner = defaultdict(set)
    for ba in PartnerBankAccount.objects.filter(partner_id__in=partner_ids, is_active=True):
        ibans_by_partner[ba.partner_id].add((ba.iban or '').replace(' ', '').upper())

    def _gfk_map(qs, ct, ids):
        result = defaultdict(list)
        for row in qs.filter(tenant=tenant, source_content_type=ct, source_object_id__in=ids):
            result[row.source_object_id].append(row)
        return result

    je_out = _gfk_map(JournalEntry.all_objects.select_related('fiscal_period'), invoice_ct, outgoing_ids)
    je_in = _gfk_map(JournalEntry.all_objects.select_related('fiscal_period'), expense_ct, incoming_ids)
    je_dep = _gfk_map(JournalEntry.all_objects.select_related('fiscal_period'), deposit_ct, deposit_ids)
    sub_out = _gfk_map(SubledgerItem.all_objects, invoice_ct, outgoing_ids)
    sub_in = _gfk_map(SubledgerItem.all_objects, expense_ct, incoming_ids)
    sub_dep = _gfk_map(SubledgerItem.all_objects, deposit_ct, deposit_ids)
    vat_out = _gfk_map(
        VATLedgerEntry.all_objects.select_related('vat_period'),
        invoice_ct,
        outgoing_ids,
    )
    vat_in = _gfk_map(
        VATLedgerEntry.all_objects.select_related('vat_period'),
        expense_ct,
        incoming_ids,
    )

    je_ids = [
        e.pk
        for rows in list(je_out.values()) + list(je_in.values()) + list(je_dep.values())
        for e in rows
    ]
    lines_by_je = defaultdict(list)
    for line in JournalEntryLine.objects.filter(journal_entry_id__in=je_ids).select_related('account'):
        lines_by_je[line.journal_entry_id].append(line)

    sub_ids = [
        s.pk
        for rows in list(sub_out.values()) + list(sub_in.values()) + list(sub_dep.values())
        for s in rows
    ]
    alloc_by_sub = defaultdict(list)
    for alloc in SubledgerAllocation.all_objects.filter(tenant=tenant, subledger_item_id__in=sub_ids):
        alloc_by_sub[alloc.subledger_item_id].append(alloc)

    payments_by_invoice = defaultdict(list)
    for pay in Payment.all_objects.filter(tenant=tenant, related_invoice_id__in=outgoing_ids):
        payments_by_invoice[pay.related_invoice_id].append(pay)
    payment_ids = [p.pk for rows in payments_by_invoice.values() for p in rows]

    bank_by_payment = defaultdict(list)
    for tx in BankTransaction.all_objects.filter(tenant=tenant, matched_payment_id__in=payment_ids):
        bank_by_payment[tx.matched_payment_id].append(tx)
    bank_by_je = defaultdict(list)
    for tx in BankTransaction.all_objects.filter(tenant=tenant, matched_journal_entry_id__in=je_ids):
        bank_by_je[tx.matched_journal_entry_id].append(tx)

    orders_by_payment = {}
    for order in PaymentOrder.all_objects.filter(tenant=tenant, payment_id__in=payment_ids):
        orders_by_payment.setdefault(order.payment_id, order)

    def _as4_map(ct, ids):
        result = defaultdict(list)
        for link in As4DocumentLink.all_objects.filter(tenant=tenant, content_type=ct, object_id__in=ids):
            result[link.object_id].append(link)
        return result

    def _super_map(ct, ids):
        result = defaultdict(list)
        for link in SuperDocumentLink.all_objects.filter(tenant=tenant, content_type=ct, object_id__in=ids):
            result[link.object_id].append(link)
        return result

    fiscal_by_invoice = defaultdict(list)
    for log in FiscalSubmissionLog.all_objects.filter(tenant=tenant, invoice_id__in=outgoing_ids):
        fiscal_by_invoice[log.invoice_id].append(log)
    outbox_by_invoice = defaultdict(list)
    for msg in IntegrationOutboxMessage.all_objects.filter(tenant=tenant, invoice_id__in=outgoing_ids):
        outbox_by_invoice[msg.invoice_id].append(msg)

    period_ids = set()
    for rows in list(vat_out.values()) + list(vat_in.values()):
        for row in rows:
            period_ids.add(row.vat_period_id)
    vat_returns = list(
        VATReturn.all_objects.filter(tenant=tenant, vat_period_id__in=period_ids).order_by(
            'vat_period_id', 'version',
        )
    )
    return_ids = [vr.pk for vr in vat_returns]
    vat_return_ct = _ct(VATReturn)
    events_by_return = defaultdict(list)
    if return_ids:
        for event in SubmissionEvent.all_objects.filter(
            tenant=tenant,
            content_type=vat_return_ct,
            object_id__in=return_ids,
        ).order_by('submission_no'):
            events_by_return[event.object_id].append(event)

    returns_by_period = defaultdict(list)
    for vat_return in vat_returns:
        returns_by_period[vat_return.vat_period_id].append(vat_return)

    # Duplicate heuristic for page (full-set duplicate filter is SQL).
    return {
        'invoices': invoices,
        'expenses': expenses,
        'deposits': deposits,
        'items_by_invoice': items_by_invoice,
        'attachments_by_expense': attachments_by_expense,
        'partners': partners,
        'ibans_by_partner': ibans_by_partner,
        'je_out': je_out,
        'je_in': je_in,
        'je_dep': je_dep,
        'sub_out': sub_out,
        'sub_in': sub_in,
        'sub_dep': sub_dep,
        'vat_out': vat_out,
        'vat_in': vat_in,
        'lines_by_je': lines_by_je,
        'alloc_by_sub': alloc_by_sub,
        'payments_by_invoice': payments_by_invoice,
        'bank_by_payment': bank_by_payment,
        'bank_by_je': bank_by_je,
        'orders_by_payment': orders_by_payment,
        'as4_out': _as4_map(invoice_ct, outgoing_ids),
        'as4_in': _as4_map(expense_ct, incoming_ids),
        'super_out': _super_map(invoice_ct, outgoing_ids),
        'super_in': _super_map(expense_ct, incoming_ids),
        'fiscal_by_invoice': fiscal_by_invoice,
        'outbox_by_invoice': outbox_by_invoice,
        'returns_by_period': returns_by_period,
        'events_by_return': events_by_return,
    }
