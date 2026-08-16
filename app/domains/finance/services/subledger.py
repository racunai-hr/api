"""Saldakonti — open items i alokacije uplata (Finance domain)."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from accounting.models import JournalEntry, SubledgerAllocation, SubledgerItem


def _document_total(source) -> Decimal:
    if hasattr(source, 'total_amount') and source.total_amount is not None:
        return Decimal(source.total_amount)
    return Decimal(getattr(source, 'amount', 0) or 0)


def _document_due_date(source):
    due = getattr(source, 'due_date', None)
    if due is not None:
        return due
    return (
        getattr(source, 'issue_date', None)
        or getattr(source, 'expense_date', None)
        or timezone.now().date()
    )


def _refresh_item_status(item: SubledgerItem) -> None:
    if item.status == 'cancelled':
        return
    if item.open_amount <= Decimal('0'):
        item.open_amount = Decimal('0')
        item.status = 'closed'
        if item.closed_at is None:
            item.closed_at = timezone.now()
    elif item.open_amount < item.original_amount:
        item.status = 'partial'
        item.closed_at = None
    else:
        item.status = 'open'
        item.closed_at = None


def get_subledger_item_for_source(tenant, source) -> SubledgerItem | None:
    ct = ContentType.objects.get_for_model(source)
    return SubledgerItem.all_objects.filter(
        tenant=tenant,
        source_content_type=ct,
        source_object_id=source.pk,
    ).exclude(status='cancelled').first()


def create_subledger_item(
    tenant,
    *,
    partner,
    direction: str,
    source,
    journal_entry: JournalEntry,
    amount: Decimal | None = None,
    due_date=None,
) -> SubledgerItem | None:
    """Kreira otvorenu stavku saldakonta za izdani račun ili odobren trošak."""
    if partner is None:
        return None
    if amount is None:
        amount = _document_total(source)
    if amount <= Decimal('0'):
        return None

    ct = ContentType.objects.get_for_model(source)
    existing = SubledgerItem.all_objects.filter(
        tenant=tenant,
        source_content_type=ct,
        source_object_id=source.pk,
    ).exclude(status='cancelled').first()
    if existing:
        return existing

    if due_date is None:
        due_date = _document_due_date(source)

    return SubledgerItem.all_objects.create(
        tenant=tenant,
        partner=partner,
        direction=direction,
        source_content_type=ct,
        source_object_id=source.pk,
        journal_entry=journal_entry,
        original_amount=amount,
        open_amount=amount,
        due_date=due_date,
        status='open',
    )


def allocate_payment(
    tenant,
    *,
    source,
    journal_entry: JournalEntry,
    amount: Decimal | None = None,
) -> SubledgerAllocation | None:
    """Smanjuje otvoreni iznos stavke saldakonta za uplatu/isplatu."""
    item = get_subledger_item_for_source(tenant, source)
    if item is None or item.status == 'cancelled':
        return None

    if amount is None:
        amount = _document_total(source)
    amount = Decimal(amount or 0)
    if amount <= Decimal('0'):
        return None

    if SubledgerAllocation.all_objects.filter(journal_entry=journal_entry).exists():
        return None

    applied = min(amount, item.open_amount)
    if applied <= Decimal('0'):
        return None

    allocation = SubledgerAllocation.all_objects.create(
        tenant=tenant,
        subledger_item=item,
        journal_entry=journal_entry,
        amount=applied,
    )
    item.open_amount -= applied
    _refresh_item_status(item)
    item.save(update_fields=['open_amount', 'status', 'closed_at'])
    return allocation


def cancel_subledger_item(item: SubledgerItem) -> None:
    if item.status == 'cancelled':
        return
    item.status = 'cancelled'
    item.open_amount = Decimal('0')
    item.closed_at = timezone.now()
    item.save(update_fields=['status', 'open_amount', 'closed_at'])


def reverse_allocation(allocation: SubledgerAllocation) -> None:
    item = allocation.subledger_item
    if item.status != 'cancelled':
        item.open_amount += allocation.amount
        if item.open_amount > item.original_amount:
            item.open_amount = item.original_amount
        _refresh_item_status(item)
        item.save(update_fields=['open_amount', 'status', 'closed_at'])
    allocation.delete()


def handle_journal_entry_reversal(entry: JournalEntry) -> None:
    """Poništi stavke saldakonta ili vrati alokacije pri stornu temeljnice."""
    for allocation in list(
        SubledgerAllocation.all_objects.filter(journal_entry=entry).select_related('subledger_item')
    ):
        reverse_allocation(allocation)

    for item in SubledgerItem.all_objects.filter(journal_entry=entry):
        cancel_subledger_item(item)


def sync_subledger_for_document_posting(
    tenant,
    source,
    document_type: str,
    journal_entry: JournalEntry,
) -> None:
    """Hook nakon ``post_document`` — kreira stavku ili alocira plaćanje."""
    if document_type == 'invoice_issued':
        partner = getattr(source, 'company_to', None)
        create_subledger_item(
            tenant,
            partner=partner,
            direction='receivable',
            source=source,
            journal_entry=journal_entry,
        )
    elif document_type == 'expense_approved':
        partner = getattr(source, 'supplier', None)
        create_subledger_item(
            tenant,
            partner=partner,
            direction='payable',
            source=source,
            journal_entry=journal_entry,
        )
    elif document_type in ('invoice_paid', 'expense_paid'):
        allocate_payment(
            tenant,
            source=source,
            journal_entry=journal_entry,
        )


def sync_subledger_for_invoice_payment(
    tenant,
    invoice,
    payment,
    journal_entry: JournalEntry,
) -> None:
    """Hook nakon ``post_invoice_payment`` — alocira djelomičnu uplatu."""
    amount = Decimal(getattr(payment, 'amount', 0) or 0)
    allocate_payment(
        tenant,
        source=invoice,
        journal_entry=journal_entry,
        amount=amount,
    )
