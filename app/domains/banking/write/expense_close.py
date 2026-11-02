"""Approve a draft expense onto chosen accounts and settle it with one bank debit."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import Http404

from accounting.models import SubledgerItem
from banking.book import transaction_book_amount, transaction_book_currency
from banking.models import BankTransaction
from banking.reconciliation import MatchConflict, match_transaction_to_journal_entry
from domains.banking.write.reconcile import list_expense_close_payments
from domains.finance.ai.account_suggest import suggest_expense_accounts
from domains.finance.facade import (
    SettlementBadRequest,
    SettlementConflict,
    settle_open_item_from_bank,
)
from domains.finance.services.account_resolver import ExpenseAccountResolutionError
from domains.finance.services.expenses import (
    ExpenseApproveBadRequest,
    ExpenseApproveConflict,
    approve_expense_for_posting,
)
from domains.finance.services.posting_memory import (
    apply_line_accounts,
    expense_line_facts,
    remembered_account_ids,
    remember_line_accounts,
)
from domains.finance.services.subledger import get_subledger_item_for_source
from expenses.models import Expense, ExpenseBankCloseIdempotency


class CloseError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def expense_close_preview(tenant, expense_id: int) -> dict:
    expense = _load_expense(tenant, expense_id)
    _require_draft(expense)
    suggestion = suggest_expense_accounts(expense)
    return {
        'remembered': suggestion['remembered'],
        'lines': suggestion['lines'],
        'payments': list_expense_close_payments(tenant, expense),
    }


def close_expense_from_bank(
    *,
    tenant,
    user,
    expense_id: int,
    bank_transaction_id: int,
    lines,
    remember: bool,
    idempotency_key: str,
    answers=None,
) -> dict:
    key = (idempotency_key or '').strip()
    if not key:
        raise CloseError(422, 'Header Idempotency-Key je obavezan.')
    if len(key) > 128:
        raise CloseError(422, 'Idempotency-Key je predugačak.')
    try:
        tx_id = int(bank_transaction_id)
    except (TypeError, ValueError):
        raise CloseError(422, 'bank_transaction_id mora biti cijeli broj.')

    try:
        with transaction.atomic():
            expense = (
                Expense.all_objects.select_for_update()
                .filter(tenant=tenant, pk=expense_id)
                .select_related('supplier', 'category')
                .first()
            )
            if expense is None:
                raise Http404()
            existing = (
                ExpenseBankCloseIdempotency.all_objects.select_for_update()
                .filter(tenant=tenant, idempotency_key=key)
                .first()
            )
            if existing is not None:
                if existing.expense_id != expense.pk:
                    raise CloseError(409, 'Idempotency-Key je već iskorišten za drugi račun.')
                return existing.result_payload

            _require_draft(expense)
            profile = _apply_accounting_review(tenant, user, expense, answers, lines)
            if profile not in {
                'goods_inward_cost',
                'company_passenger_vehicle',
                'full_vat_vehicle',
                'vehicle_inventory_cost',
            }:
                stored = remembered_account_ids(expense)
                account_ids = _account_ids(expense, lines, remembered=stored)
                try:
                    apply_line_accounts(expense, account_ids)
                except ExpenseAccountResolutionError as exc:
                    raise CloseError(422, _validation_text(exc)) from exc
                if remember and stored is None:
                    remember_line_accounts(expense, account_ids)

            try:
                approve_expense_for_posting(tenant=tenant, expense_id=expense.pk, user=user)
            except ExpenseApproveBadRequest as exc:
                raise CloseError(422, exc.detail) from exc
            except ExpenseApproveConflict as exc:
                raise CloseError(409, exc.detail) from exc
            except ExpenseAccountResolutionError as exc:
                raise CloseError(422, _validation_text(exc)) from exc

            expense.refresh_from_db()
            bank_tx = _lock_exact_debit(tenant, tx_id, expense)
            item = get_subledger_item_for_source(tenant, expense)
            if item is None:
                raise CloseError(422, 'Knjiženje nije otvorilo saldakonto.')
            item = SubledgerItem.all_objects.select_for_update().get(pk=item.pk)
            book_amount = transaction_book_amount(bank_tx)
            if Decimal(item.open_amount) != book_amount:
                raise CloseError(422, 'Uplata mora biti točnog iznosa.')
            try:
                settlement = settle_open_item_from_bank(
                    tenant=tenant,
                    user=user,
                    subledger_item=item,
                    bank_account=bank_tx.bank_statement.bank_account,
                    settlement_amount=book_amount,
                    settlement_date=bank_tx.transaction_date,
                    transaction_type=bank_tx.transaction_type,
                    bank_transaction_id=bank_tx.pk,
                    locked_source=expense,
                    source_amount=bank_tx.amount,
                    source_currency=bank_tx.currency,
                )
            except SettlementBadRequest as exc:
                raise CloseError(422, exc.detail) from exc
            except SettlementConflict as exc:
                raise CloseError(409, exc.detail) from exc
            try:
                match_transaction_to_journal_entry(bank_tx, settlement.journal_entry, user)
            except MatchConflict as exc:
                raise CloseError(409, _validation_text(exc)) from exc
            except DjangoValidationError as exc:
                raise CloseError(422, _validation_text(exc)) from exc

            expense.refresh_from_db()
            item.refresh_from_db()
            bank_tx.refresh_from_db()
            payload = {
                'expense_id': expense.pk,
                'status': expense.status,
                'bank_transaction_id': bank_tx.pk,
                'match_status': bank_tx.match_status,
                'subledger_status': item.status,
            }
            ExpenseBankCloseIdempotency.all_objects.create(
                tenant=tenant,
                idempotency_key=key,
                expense=expense,
                result_payload=payload,
            )
            return payload
    except IntegrityError as exc:
        existing = ExpenseBankCloseIdempotency.all_objects.filter(
            tenant=tenant,
            idempotency_key=key,
        ).first()
        if existing is None:
            raise
        if existing.expense_id != expense_id:
            raise CloseError(409, 'Idempotency-Key je već iskorišten za drugi račun.') from exc
        return existing.result_payload


def _apply_accounting_review(tenant, user, expense, answers, lines) -> str:
    from domains.finance.services.accounting_review import (
        VEHICLE_PROFILES,
        AccountingReviewBlocked,
        bind_review_choice,
        locked_account_detail,
        require_posting_review,
        review_expense,
    )
    from expenses.models import ACCOUNT_LOCKED_PROFILES

    review = review_expense(tenant, expense, answers, user)
    try:
        require_posting_review(review)
    except AccountingReviewBlocked as exc:
        raise CloseError(422, exc.detail) from exc
    profile = review.get('posting_profile') or ''
    if profile in ACCOUNT_LOCKED_PROFILES:
        if _lines_have_accounts(lines):
            raise CloseError(422, locked_account_detail(profile))
        bind_review_choice(expense, review, answers)
        return profile
    if profile in VEHICLE_PROFILES:
        bind_review_choice(expense, review, answers)
        if _lines_have_accounts(lines):
            try:
                apply_line_accounts(expense, _account_ids(expense, lines, remembered=None))
            except ExpenseAccountResolutionError as exc:
                raise CloseError(422, _validation_text(exc)) from exc
        return profile
    if profile:
        expense.posting_profile = profile
        expense.save(update_fields=['posting_profile', 'updated_at'])
    return profile


def _lines_have_accounts(lines) -> bool:
    if not isinstance(lines, list):
        return False
    for item in lines:
        if isinstance(item, dict) and item.get('account_id'):
            return True
    return False


def _load_expense(tenant, expense_id: int) -> Expense:
    expense = (
        Expense.all_objects.filter(tenant=tenant, pk=expense_id)
        .select_related('supplier', 'category')
        .first()
    )
    if expense is None:
        raise Http404()
    return expense


def _require_draft(expense: Expense) -> None:
    if expense.status != 'draft':
        raise CloseError(422, 'Samo nacrt se može zatvoriti uplatom.')
    if expense.supplier_id is None:
        raise CloseError(422, 'Račun nema dobavljača.')


def _account_ids(expense: Expense, lines, *, remembered: dict[int, int] | None) -> dict[int, int]:
    if remembered is not None:
        return remembered
    if not isinstance(lines, list) or not lines:
        raise CloseError(422, 'Odaberi konto za svaku stavku.')
    chosen: dict[int, int] = {}
    for item in lines:
        if not isinstance(item, dict):
            raise CloseError(422, 'Stavka konta nije ispravna.')
        try:
            position = int(item.get('position'))
            account_id = int(item.get('account_id'))
        except (TypeError, ValueError):
            raise CloseError(422, 'Stavka konta nije ispravna.')
        chosen[position] = account_id
    expected = set(range(1, len(expense_line_facts(expense)) + 1))
    if set(chosen) != expected:
        raise CloseError(422, 'Konta moraju pokriti sve stavke.')
    return chosen


def _lock_exact_debit(tenant, tx_id: int, expense: Expense) -> BankTransaction:
    bank_tx = (
        BankTransaction.all_objects.select_for_update(of=('self',))
        .filter(tenant=tenant, pk=tx_id)
        .select_related('bank_statement__bank_account__ledger_account')
        .first()
    )
    if bank_tx is None:
        raise Http404()
    amount = Decimal(expense.amount).quantize(Decimal('0.01'))
    currency = (expense.currency or 'EUR').upper()
    if bank_tx.transaction_type != 'debit':
        raise CloseError(422, 'Obveza se zatvara terećenjem.')
    if bank_tx.match_status != 'unmatched' or bank_tx.matched_journal_entry_id or bank_tx.matched_payment_id:
        raise CloseError(422, 'Uplata je već usklađena.')
    if transaction_book_amount(bank_tx) != amount or transaction_book_currency(bank_tx) != currency:
        raise CloseError(422, 'Uplata mora biti točnog iznosa.')
    return bank_tx


def _validation_text(exc: Exception) -> str:
    if hasattr(exc, 'message_dict') and exc.message_dict:
        return str(exc.message_dict)
    if hasattr(exc, 'messages'):
        messages = list(exc.messages)
        if len(messages) == 1:
            return str(messages[0])
        return str(messages)
    detail = getattr(exc, 'detail', None)
    if isinstance(detail, str) and detail:
        return detail
    return str(exc)
