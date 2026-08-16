from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db import transaction

from expenses.models import (
    Expense,
    ExpenseCategory,
    ExpenseImportMetadata,
    ExpensePayer,
    ExpenseSource,
    ImportBatch,
    ReimbursementStatus,
    SettlementMethod,
)
from expenses.parsers.types import NormalizedExpenseRow
from expenses.services.numbering import assign_expense_number
from expenses.services.partner_resolver import resolve_partner


@dataclass(frozen=True)
class ImportRowIssue:
    row_number: int
    receipt_number: str
    message: str


@dataclass(frozen=True)
class ImportResult:
    batch_id: int | None
    dry_run: bool
    rows_total: int
    created: int
    duplicates: int
    errors: int
    duplicate_receipts: tuple[str, ...]
    error_details: tuple[ImportRowIssue, ...]


def _default_category(tenant) -> ExpenseCategory:
    category, _ = ExpenseCategory.all_objects.get_or_create(
        tenant=tenant,
        name='Ostalo',
        defaults={'description': 'Ostali troškovi'},
    )
    return category


def _is_metadata_duplicate(tenant, row: NormalizedExpenseRow) -> bool:
    return ExpenseImportMetadata.all_objects.filter(
        tenant=tenant,
        source=row.source,
        external_id=row.external_id,
    ).exists()


def _is_expense_fallback_duplicate(tenant, row: NormalizedExpenseRow) -> bool:
    return Expense.all_objects.filter(
        tenant=tenant,
        supplier__tax_number=row.supplier_oib,
        receipt_number=row.receipt_number,
        expense_date=row.expense_date,
        amount=row.amount,
    ).exists()


def _resolve_reimbursement_status(
    settlement_method: str,
    status: str,
    reimbursement_status: str,
) -> str:
    private_methods = {SettlementMethod.PRIVATE_CARD, SettlementMethod.PRIVATE_CASH}
    if settlement_method in private_methods and status == 'paid':
        return reimbursement_status or ReimbursementStatus.PENDING
    if reimbursement_status:
        return reimbursement_status
    return ReimbursementStatus.NOT_REQUIRED


def _validate_private_settlement(settlement_method: str, status: str, paid_by: ExpensePayer | None) -> None:
    private_methods = {SettlementMethod.PRIVATE_CARD, SettlementMethod.PRIVATE_CASH}
    if settlement_method in private_methods and status == 'paid' and paid_by is None:
        raise ValueError('paid_by je obavezan za privatno podmirenje plaćenog troška.')


@transaction.atomic
def import_expense_rows(
    *,
    tenant,
    user,
    rows: Iterable[NormalizedExpenseRow],
    source: str,
    filename: str = '',
    dry_run: bool = False,
    batch: ImportBatch | None = None,
    status: str = '',
    settlement_method: str = '',
    paid_by: ExpensePayer | None = None,
    reimbursement_status: str = '',
) -> ImportResult:
    row_list = list(rows)
    category = _default_category(tenant)

    if batch is None:
        batch = ImportBatch.all_objects.create(
            tenant=tenant,
            source=source,
            filename=filename,
            uploaded_by=user,
            dry_run=dry_run,
            rows_total=len(row_list),
        )
    else:
        batch.rows_total = (batch.rows_total or 0) + len(row_list)
        batch.save(update_fields=['rows_total'])

    created = 0
    duplicates = 0
    errors = 0
    duplicate_receipts: list[str] = []
    error_details: list[ImportRowIssue] = []

    for row in row_list:
        try:
            if _is_metadata_duplicate(tenant, row) or _is_expense_fallback_duplicate(tenant, row):
                duplicates += 1
                duplicate_receipts.append(row.receipt_number)
                continue

            if dry_run:
                _validate_private_settlement(
                    settlement_method,
                    status or row.status,
                    paid_by,
                )
                created += 1
                continue

            supplier = resolve_partner(
                tenant=tenant,
                oib=row.supplier_oib,
                name=row.supplier_name,
            )
            expense_status = status or row.status
            _validate_private_settlement(settlement_method, expense_status, paid_by)
            expense = Expense.all_objects.create(
                tenant=tenant,
                expense_number=assign_expense_number(tenant, year=row.expense_date.year),
                source=row.source,
                payment_method=row.payment_method,
                settlement_method=settlement_method,
                paid_by=paid_by,
                reimbursement_status=_resolve_reimbursement_status(
                    settlement_method,
                    expense_status,
                    reimbursement_status,
                ),
                status=expense_status,
                category=category,
                supplier=supplier,
                amount=row.amount,
                tax_amount=row.tax_amount,
                currency=row.currency,
                expense_date=row.expense_date,
                due_date=row.due_date,
                receipt_number=row.receipt_number,
                description=row.description,
                created_by=user,
            )
            ExpenseImportMetadata.all_objects.create(
                tenant=tenant,
                expense=expense,
                batch=batch,
                source=row.source,
                external_id=row.external_id,
                jir=row.jir,
                super_guid=row.super_guid,
                raw_payload=row.raw_payload,
            )
            created += 1
        except Exception as exc:
            errors += 1
            error_details.append(
                ImportRowIssue(
                    row_number=row.row_number,
                    receipt_number=row.receipt_number,
                    message=str(exc),
                )
            )

    batch.created_count = (batch.created_count or 0) + created
    batch.duplicates_count = (batch.duplicates_count or 0) + duplicates
    batch.errors_count = (batch.errors_count or 0) + errors
    batch.report_payload = {
        'duplicate_receipts': duplicate_receipts,
        'errors': [
            {'row_number': issue.row_number, 'receipt_number': issue.receipt_number, 'message': issue.message}
            for issue in error_details
        ],
    }
    batch.save(update_fields=['created_count', 'duplicates_count', 'errors_count', 'report_payload', 'rows_total'])

    return ImportResult(
        batch_id=batch.pk,
        dry_run=dry_run,
        rows_total=len(row_list),
        created=created,
        duplicates=duplicates,
        errors=errors,
        duplicate_receipts=tuple(duplicate_receipts),
        error_details=tuple(error_details),
    )
