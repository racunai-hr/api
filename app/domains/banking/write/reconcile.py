"""Explicit reconcile BankTransaction ↔ open SubledgerItem (ADR-0025)."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.http import Http404

from accounting.models import Deposit, SubledgerItem
from banking.models import BankReconcileIdempotency, BankTransaction
from banking.reconciliation import MatchConflict, match_transaction_to_journal_entry
from domains.banking.read.dto import transaction_dto
from domains.finance.facade import (
    SettlementBadRequest,
    SettlementConflict,
    settle_open_item_from_bank,
)
from domains.finance.services.aging import _subledger_source_label
from expenses.models import Expense
from invoices.models import Invoice


class AlreadyReconciled(MatchConflict):
    """Bank transaction already matched — do not re-run Finance settlement."""


class ReconcileIdempotencyConflict(MatchConflict):
    """Same Idempotency-Key with a different subledger_item_id."""


def _abs_amount(bank_tx: BankTransaction) -> Decimal:
    return abs(Decimal(bank_tx.amount)).quantize(Decimal('0.01'))


def _expected_direction(transaction_type: str) -> str:
    return 'receivable' if transaction_type == 'credit' else 'payable'


def _action_label(source_type: str, direction: str) -> str:
    if source_type == 'deposit':
        return 'Povrat kaucije'
    if direction == 'receivable':
        return 'Zatvori potraživanje'
    return 'Zatvori obvezu'


def list_open_item_candidates(*, tenant, transaction_id: int, q: str | None = None) -> dict:
    bank_tx = (
        BankTransaction.all_objects.filter(tenant=tenant, pk=transaction_id)
        .select_related('bank_statement__bank_account')
        .first()
    )
    if bank_tx is None:
        raise Http404()

    amount = _abs_amount(bank_tx)
    direction = _expected_direction(bank_tx.transaction_type)
    qs = (
        SubledgerItem.all_objects.filter(
            tenant=tenant,
            status__in=('open', 'partial'),
            direction=direction,
        )
        .select_related('partner', 'source_content_type')
        .order_by('due_date', 'id')
    )
    if q:
        term = q.strip()
        if term:
            qs = qs.filter(
                Q(partner__name__icontains=term)
                | Q(partner__tax_number__icontains=term)
                | Q(partner__vat_number__icontains=term)
            )

    results = []
    for item in qs[:80]:
        source_type = item.source_content_type.model if item.source_content_type_id else ''
        if source_type == 'deposit':
            if bank_tx.transaction_type != 'credit' or item.open_amount != amount:
                continue
        elif source_type in ('invoice', 'expense'):
            if item.open_amount < amount:
                continue
        else:
            continue
        results.append({
            'item_id': item.pk,
            'partner_id': item.partner_id,
            'partner_name': item.partner.name if item.partner_id else '',
            'direction': item.direction,
            'source_type': source_type,
            'source_id': item.source_object_id,
            'source_label': _subledger_source_label(item),
            'open_amount': f'{item.open_amount:.2f}',
            'due_date': item.due_date.isoformat() if item.due_date else None,
            'action_label': _action_label(source_type, item.direction),
        })
        if len(results) >= 50:
            break
    return {'count': len(results), 'results': results}


def reconcile_open_item_api(
    *,
    tenant,
    user,
    transaction_id: int,
    subledger_item_id: int,
    idempotency_key: str,
) -> dict:
    key = (idempotency_key or '').strip()
    if not key:
        from django.core.exceptions import ValidationError

        raise ValidationError({'Idempotency-Key': 'Header Idempotency-Key je obavezan.'})
    if not subledger_item_id:
        from django.core.exceptions import ValidationError

        raise ValidationError({'subledger_item_id': 'subledger_item_id je obavezan.'})

    with transaction.atomic():
        bank_tx = (
            BankTransaction.all_objects.select_for_update(of=('self',))
            .filter(tenant=tenant, pk=transaction_id)
            .select_related('bank_statement__bank_account__ledger_account')
            .first()
        )
        if bank_tx is None:
            raise Http404()

        existing_key = (
            BankReconcileIdempotency.all_objects.select_for_update()
            .filter(tenant=tenant, bank_transaction=bank_tx, idempotency_key=key)
            .first()
        )
        if existing_key is not None:
            if existing_key.subledger_item_id != subledger_item_id:
                raise ReconcileIdempotencyConflict(
                    'Isti Idempotency-Key već je korišten s drugim subledger_item_id.'
                )
            return existing_key.result_payload

        if bank_tx.match_status == 'matched' and (
            bank_tx.matched_journal_entry_id or bank_tx.matched_payment_id
        ):
            raise AlreadyReconciled('Transakcija je već usklađena.')

        item = (
            SubledgerItem.all_objects.select_for_update()
            .filter(tenant=tenant, pk=subledger_item_id)
            .select_related('source_content_type', 'partner')
            .first()
        )
        if item is None:
            raise Http404()

        # Lock source in fixed order after SubledgerItem
        model = item.source_content_type.model if item.source_content_type_id else ''
        locked_source = None
        if model == 'deposit':
            locked_source = (
                Deposit.all_objects.select_for_update(of=('self',))
                .filter(tenant=tenant, pk=item.source_object_id)
                .select_related('partner')
                .first()
            )
            if locked_source is None:
                raise Http404()
        elif model == 'invoice':
            locked_source = (
                Invoice.all_objects.select_for_update()
                .filter(tenant=tenant, pk=item.source_object_id)
                .select_related('company_to')
                .first()
            )
            if locked_source is None:
                raise Http404()
        elif model == 'expense':
            locked_source = (
                Expense.all_objects.select_for_update()
                .filter(tenant=tenant, pk=item.source_object_id)
                .select_related('supplier')
                .first()
            )
            if locked_source is None:
                raise Http404()
        else:
            from django.core.exceptions import ValidationError

            raise ValidationError({'subledger_item_id': f'Nepodržan izvor: {model}'})

        bank_account = bank_tx.bank_statement.bank_account
        amount = _abs_amount(bank_tx)

        try:
            settlement = settle_open_item_from_bank(
                tenant=tenant,
                user=user,
                subledger_item=item,
                bank_account=bank_account,
                settlement_amount=amount,
                settlement_date=bank_tx.transaction_date,
                transaction_type=bank_tx.transaction_type,
                bank_transaction_id=bank_tx.pk,
                locked_source=locked_source,
            )
        except SettlementConflict as exc:
            raise MatchConflict(exc.detail) from exc
        except SettlementBadRequest as exc:
            from django.core.exceptions import ValidationError

            raise ValidationError({'detail': exc.detail, 'code': exc.code}) from exc

        match_transaction_to_journal_entry(bank_tx, settlement.journal_entry, user)
        bank_tx.refresh_from_db()
        payload = transaction_dto(bank_tx)
        payload['reconciled_source_type'] = settlement.source_type
        payload['reconciled_source_id'] = settlement.source_id
        payload['settlement_journal_entry_id'] = settlement.journal_entry.pk

        BankReconcileIdempotency.all_objects.create(
            tenant=tenant,
            bank_transaction=bank_tx,
            idempotency_key=key,
            subledger_item_id=subledger_item_id,
            result_payload=payload,
        )
        return payload
