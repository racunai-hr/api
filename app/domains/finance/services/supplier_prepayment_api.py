"""HTTP orchestration for prepayment candidates and apply. The engine stays document-agnostic."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import Http404

from accounting.models import PrepaymentVatTreatment, PurchaseNature, SupplierPrepayment
from banking.reconciliation import MatchConflict
from domains.banking.write.expense_close import CloseError, _account_ids
from domains.finance.services.account_resolver import ExpenseAccountResolutionError
from domains.finance.services.expenses import (
    ExpenseApproveBadRequest,
    ExpenseApproveConflict,
    approve_expense_for_posting,
)
from domains.finance.services.payable_documents import payable_model, resolve_payable_document
from domains.finance.services.posting_memory import (
    apply_line_accounts,
    remembered_account_ids,
    remember_line_accounts,
)
from domains.finance.services.subledger import get_subledger_item_for_source
from domains.finance.services.supplier_prepayments import (
    apply_supplier_prepayment,
    post_supplier_prepayment,
)
from expenses.models import Expense


class PrepaymentApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _text(exc: Exception) -> str:
    if isinstance(exc, (CloseError, ExpenseApproveBadRequest, ExpenseApproveConflict)):
        return exc.detail
    if hasattr(exc, 'messages'):
        messages = list(exc.messages)
        if len(messages) == 1:
            return str(messages[0])
        return str(messages)
    return str(exc)


def _load_source(tenant, source_type: str, source_id: int):
    try:
        model = payable_model(source_type)
    except DjangoValidationError as exc:
        raise PrepaymentApiError(404, _text(exc)) from exc
    source = model.all_objects.filter(tenant=tenant, pk=source_id).first()
    if source is None:
        raise Http404()
    return source


def list_prepayment_candidates(tenant, source_type: str, source_id: int) -> dict:
    source = _load_source(tenant, source_type, source_id)
    document = resolve_payable_document(source)
    partner = document.partner
    rows = []
    if partner is not None:
        candidates = (
            SupplierPrepayment.all_objects.filter(
                tenant=tenant,
                partner=partner,
                currency=document.currency or 'EUR',
                status__in=(SupplierPrepayment.STATUS_OPEN, SupplierPrepayment.STATUS_PARTIAL),
            )
            .select_related('prepayment_account')
            .order_by('posting_date', 'id')
        )
        for prepayment in candidates:
            if prepayment.available_amount <= Decimal('0'):
                continue
            rows.append({
                'id': prepayment.pk,
                'posting_date': prepayment.posting_date.isoformat(),
                'original_amount': str(prepayment.original_amount),
                'available_amount': str(prepayment.available_amount),
                'currency': prepayment.currency,
                'purchase_nature': prepayment.purchase_nature,
                'vat_treatment': prepayment.vat_treatment,
                'account_code': prepayment.prepayment_account.account_code,
            })
    return {
        'supplier_id': getattr(partner, 'pk', None),
        'currency': document.currency or 'EUR',
        'ubl_prepaid_amount': _money_or_none(getattr(source, 'ubl_prepaid_amount', None)),
        'ubl_payable_amount': _money_or_none(getattr(source, 'ubl_payable_amount', None)),
        'purchase_natures': [{'value': value, 'label': label} for value, label in PurchaseNature.choices],
        'vat_treatments': [{'value': value, 'label': label} for value, label in PrepaymentVatTreatment.choices],
        'prepayments': rows,
    }


def _money_or_none(value):
    if value is None:
        return None
    return str(value)


def post_prepayment_from_bank(
    *,
    tenant,
    user,
    bank_transaction_id: int,
    partner_id,
    purchase_nature: str,
    vat_treatment: str,
    idempotency_key: str,
) -> dict:
    key = (idempotency_key or '').strip()
    if not key:
        raise PrepaymentApiError(422, 'Header Idempotency-Key je obavezan.')
    try:
        partner_pk = int(partner_id)
    except (TypeError, ValueError) as exc:
        raise PrepaymentApiError(422, 'partner_id mora biti cijeli broj.') from exc
    try:
        prepayment = post_supplier_prepayment(
            tenant=tenant,
            user=user,
            bank_transaction_id=bank_transaction_id,
            partner_id=partner_pk,
            purchase_nature=purchase_nature or '',
            vat_treatment=vat_treatment or '',
            idempotency_key=key,
        )
    except MatchConflict as exc:
        raise PrepaymentApiError(409, _text(exc)) from exc
    except DjangoValidationError as exc:
        raise PrepaymentApiError(422, _text(exc)) from exc
    prepayment.bank_transaction.refresh_from_db()
    return {
        'id': prepayment.pk,
        'bank_transaction_id': prepayment.bank_transaction_id,
        'account_code': prepayment.prepayment_account.account_code,
        'original_amount': str(prepayment.original_amount),
        'currency': prepayment.currency,
        'status': prepayment.status,
        'match_status': prepayment.bank_transaction.match_status,
    }


@transaction.atomic
def apply_prepayments_for_source(
    *,
    tenant,
    user,
    source_type: str,
    source_id: int,
    applications,
    lines,
    remember: bool,
    idempotency_key: str,
    answers=None,
) -> dict:
    key = (idempotency_key or '').strip()
    if not key:
        raise PrepaymentApiError(422, 'Header Idempotency-Key je obavezan.')
    if not isinstance(applications, list) or not applications:
        raise PrepaymentApiError(422, 'Odaberi barem jedan predujam.')
    source = _load_source(tenant, source_type, source_id)
    if isinstance(source, Expense) and source.status == 'draft':
        from domains.finance.services.accounting_review import (
            VEHICLE_PROFILES,
            AccountingReviewBlocked,
            bind_review_choice,
            locked_account_detail,
            require_posting_review,
            review_expense,
        )
        from expenses.models import ACCOUNT_LOCKED_PROFILES

        review = review_expense(tenant, source, answers, user)
        try:
            require_posting_review(review)
        except AccountingReviewBlocked as exc:
            raise PrepaymentApiError(422, exc.detail) from exc
        profile = review.get('posting_profile') or ''
        if profile in ACCOUNT_LOCKED_PROFILES:
            if isinstance(lines, list) and any(
                isinstance(item, dict) and item.get('account_id') for item in lines
            ):
                raise PrepaymentApiError(422, locked_account_detail(profile))
            bind_review_choice(source, review, answers)
        elif profile in VEHICLE_PROFILES:
            bind_review_choice(source, review, answers)
            if isinstance(lines, list) and any(
                isinstance(item, dict) and item.get('account_id') for item in lines
            ):
                try:
                    account_ids = _account_ids(source, lines, remembered=None)
                    apply_line_accounts(source, account_ids)
                except CloseError as exc:
                    raise PrepaymentApiError(exc.status_code, exc.detail) from exc
                except ExpenseAccountResolutionError as exc:
                    raise PrepaymentApiError(422, _text(exc)) from exc
        else:
            if profile:
                source.posting_profile = profile
                source.save(update_fields=['posting_profile', 'updated_at'])
            stored = remembered_account_ids(source)
            try:
                account_ids = _account_ids(source, lines, remembered=stored)
                apply_line_accounts(source, account_ids)
            except CloseError as exc:
                raise PrepaymentApiError(exc.status_code, exc.detail) from exc
            except ExpenseAccountResolutionError as exc:
                raise PrepaymentApiError(422, _text(exc)) from exc
            if remember and stored is None:
                remember_line_accounts(source, account_ids)
        try:
            approve_expense_for_posting(tenant=tenant, expense_id=source.pk, user=user)
        except ExpenseApproveBadRequest as exc:
            raise PrepaymentApiError(422, exc.detail) from exc
        except ExpenseApproveConflict as exc:
            raise PrepaymentApiError(409, exc.detail) from exc
        except ExpenseAccountResolutionError as exc:
            raise PrepaymentApiError(422, _text(exc)) from exc
        source.refresh_from_db()

    document = resolve_payable_document(source)
    created = []
    for row in applications:
        if not isinstance(row, dict):
            raise PrepaymentApiError(422, 'Stavka predujma nije ispravna.')
        try:
            prepayment_id = int(row.get('prepayment_id'))
            amount = row.get('amount')
        except (TypeError, ValueError) as exc:
            raise PrepaymentApiError(422, 'Stavka predujma nije ispravna.') from exc
        prepayment = (
            SupplierPrepayment.all_objects.filter(tenant=tenant, pk=prepayment_id).first()
        )
        if prepayment is None:
            raise PrepaymentApiError(422, 'Predujam nije pronađen.')
        try:
            application = apply_supplier_prepayment(
                prepayment=prepayment,
                payable_document=document,
                amount=amount,
                user=user,
                idempotency_key=f'{key}:{prepayment_id}',
            )
        except DjangoValidationError as exc:
            raise PrepaymentApiError(422, _text(exc)) from exc
        created.append(application)

    source.refresh_from_db()
    item = get_subledger_item_for_source(tenant, source)
    return {
        'source_type': source_type,
        'source_id': source.pk,
        'status': getattr(source, 'status', ''),
        'subledger_status': item.status if item is not None else '',
        'applications': [
            {
                'id': application.pk,
                'prepayment_id': application.prepayment_id,
                'amount': str(application.amount),
                'journal_entry_id': application.clearing_journal_entry_id,
            }
            for application in created
        ],
    }
