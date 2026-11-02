"""Fold an inbound eRačun onto the expense that already is that invoice.

The provider document is the canonical identity (source, number, UBL, GUID).
Payment, journal, VAT and the expense primary key stay where they are.
A second row is created only when there is no single business match.
"""

from __future__ import annotations

import logging

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from expenses.models import Expense, ExpenseImportMetadata, ExpenseSource
from fiscal_gateway.services.inbound_expense import _next_expense_number
from super_integration.models import SuperDocumentLink

# Same provenance namespace as the importer. Legacy direct-sync rows share it.
_METADATA_SOURCE = ExpenseSource.SUPER

logger = logging.getLogger(__name__)


def matching_expenses(
    tenant,
    *,
    supplier_oib: str,
    receipt_number: str,
    expense_date,
    amount,
    exclude_pk: int | None = None,
) -> list[Expense]:
    """Same business invoice: supplier OIB, source number, date and gross amount.

    At most two rows are loaded. One match can be absorbed; two means the pair
    is ambiguous and must be left alone.
    """
    oib = (supplier_oib or '').replace('HR', '').strip()
    receipt = (receipt_number or '').strip()
    if not oib or not receipt or expense_date is None or amount is None:
        return []
    qs = Expense.all_objects.filter(
        tenant=tenant,
        supplier__tax_number__in=[oib, f'HR{oib}'],
        receipt_number=receipt,
        expense_date=expense_date,
        amount=amount,
    )
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return list(qs.order_by('id')[:2])


def is_unposted_draft(expense: Expense) -> bool:
    """Draft with no journal, payable or VAT row. Safe to remove after a merge."""
    if expense.status != 'draft':
        return False
    return not _has_accounting_rows(expense)


def claim_eracun_number(expense: Expense, receipt_number: str) -> str:
    """ERAC-{invoice number}, unless another expense already holds that number."""
    base = (receipt_number or 'ERAC').strip()
    candidate = f'ERAC-{base}'[:50]
    if expense.expense_number == candidate:
        return candidate
    taken = (
        Expense.all_objects.filter(tenant_id=expense.tenant_id, expense_number=candidate)
        .exclude(pk=expense.pk)
        .exists()
    )
    if not taken:
        return candidate
    return _next_expense_number(expense.tenant, base, 'ERAC')


def stamp_eracun_identity(expense: Expense, receipt_number: str) -> None:
    """Overwrite document identity. Amounts, status and accounts stay put."""
    expense.expense_number = claim_eracun_number(expense, receipt_number)
    expense.source = ExpenseSource.ERACUN
    expense._skip_auto_posting = True
    expense.save(update_fields=['expense_number', 'source', 'updated_at'])


def attach_inbound_eracun(
    tenant,
    survivor: Expense,
    *,
    item: dict,
    guid: str,
    ubl_xml: str,
    receipt_number: str,
) -> Expense:
    """Point a new provider document at an existing expense. No second row."""
    with transaction.atomic():
        stamp_eracun_identity(survivor, receipt_number)
        content_type = ContentType.objects.get_for_model(Expense)
        SuperDocumentLink.all_objects.create(
            tenant=tenant,
            direction=SuperDocumentLink.DIRECTION_INBOUND,
            super_guid=guid,
            ubl_xml=ubl_xml,
            content_type=content_type,
            object_id=survivor.pk,
        )
        ExpenseImportMetadata.all_objects.create(
            tenant=tenant,
            expense=survivor,
            source=_METADATA_SOURCE,
            external_id=guid,
            super_guid=guid,
            raw_payload=item,
        )
    return survivor


def reconcile_unposted_inbound_drafts(tenant) -> int:
    """Absorb eRačun drafts that already duplicate a booked (or older) expense.

    Runs from local rows only, so a later gateway failure cannot put the
    duplicate back.
    """
    content_type = ContentType.objects.get_for_model(Expense)
    links = SuperDocumentLink.all_objects.filter(
        tenant=tenant,
        direction=SuperDocumentLink.DIRECTION_INBOUND,
        content_type=content_type,
    )
    merged = 0
    for link in links:
        try:
            if _absorb_linked_draft(link):
                merged += 1
        except Exception:
            logger.exception('eRačun merge failed guid=%s', link.super_guid)
    return merged


def _absorb_linked_draft(link: SuperDocumentLink) -> bool:
    draft = (
        Expense.all_objects.select_related('supplier')
        .filter(pk=link.object_id, tenant_id=link.tenant_id)
        .first()
    )
    if draft is None or not is_unposted_draft(draft):
        return False
    supplier_oib = draft.supplier.tax_number if draft.supplier_id else ''
    matches = matching_expenses(
        draft.tenant,
        supplier_oib=supplier_oib,
        receipt_number=draft.receipt_number,
        expense_date=draft.expense_date,
        amount=draft.amount,
        exclude_pk=draft.pk,
    )
    if len(matches) != 1:
        return False
    return _absorb_draft(draft, matches[0], link)


@transaction.atomic
def _absorb_draft(draft: Expense, survivor: Expense, link: SuperDocumentLink) -> bool:
    if not is_unposted_draft(draft):
        return False
    _move_metadata(draft, survivor, link.super_guid)
    _move_gateway_link(draft, survivor)
    _move_rejections(draft, survivor)
    link.object_id = survivor.pk
    link.save(update_fields=['object_id'])
    # Free ERAC-{number} before the survivor claims it.
    draft.expense_number = f'~{draft.pk}'[:50]
    draft._skip_auto_posting = True
    draft.save(update_fields=['expense_number'])
    stamp_eracun_identity(survivor, draft.receipt_number)
    draft.delete()
    return True


def _move_metadata(draft: Expense, survivor: Expense, guid: str) -> None:
    (
        ExpenseImportMetadata.all_objects.filter(
            tenant_id=draft.tenant_id,
            expense=draft,
            source=_METADATA_SOURCE,
            external_id=guid,
        ).update(expense=survivor)
    )


def _move_gateway_link(draft: Expense, survivor: Expense) -> None:
    from integrations.models import InboundGatewayDocumentLink

    draft_link = InboundGatewayDocumentLink.all_objects.filter(expense=draft).first()
    if draft_link is None:
        return
    survivor_link = InboundGatewayDocumentLink.all_objects.filter(expense=survivor).first()
    if survivor_link is None:
        draft_link.expense = survivor
        draft_link.save(update_fields=['expense', 'updated_at'])
        return
    if not survivor_link.gateway_document_id and draft_link.gateway_document_id:
        survivor_link.gateway_document_id = draft_link.gateway_document_id
        survivor_link.bound_provider = draft_link.bound_provider or survivor_link.bound_provider
        survivor_link.provider_ref = draft_link.provider_ref or survivor_link.provider_ref
        survivor_link.rejection_capable = draft_link.rejection_capable
        survivor_link.rejection_capability_checked = draft_link.rejection_capability_checked
        survivor_link.save(
            update_fields=[
                'gateway_document_id',
                'bound_provider',
                'provider_ref',
                'rejection_capable',
                'rejection_capability_checked',
                'updated_at',
            ],
        )
    draft_link.delete()


def _move_rejections(draft: Expense, survivor: Expense) -> None:
    from integrations.models import InboundEracunRejectionAttempt

    pending = InboundEracunRejectionAttempt.STATUS_PENDING
    attempts = InboundEracunRejectionAttempt.all_objects.filter(expense=draft)
    survivor_pending = InboundEracunRejectionAttempt.all_objects.filter(
        expense=survivor,
        status=pending,
    ).exists()
    if survivor_pending:
        attempts.filter(status=pending).delete()
    attempts.update(expense=survivor)


def _has_accounting_rows(expense: Expense) -> bool:
    from accounting.models import JournalEntry, SubledgerItem, VATLedgerEntry

    content_type = ContentType.objects.get_for_model(Expense)
    common = {
        'tenant_id': expense.tenant_id,
        'source_content_type': content_type,
        'source_object_id': expense.pk,
    }
    return (
        JournalEntry.all_objects.filter(**common).exists()
        or SubledgerItem.all_objects.filter(**common).exists()
        or VATLedgerEntry.all_objects.filter(**common).exists()
    )
