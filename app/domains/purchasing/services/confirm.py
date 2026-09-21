from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation
from domains.purchasing.services.direction import (
    classify_document,
    direction_override_required,
    direction_unresolved,
    is_hard_own_company,
    load_company_identity,
)
from domains.purchasing.services.dto import confirm_values, import_dto
from domains.purchasing.services.exceptions import (
    DirectionOverrideRequired,
    DirectionUnresolved,
    DuplicateOverrideRequired,
    HardDuplicate,
    InvalidImportStatus,
    OwnCompanySupplier,
    PartnerRequired,
    PurchasingBadRequest,
)
from domains.purchasing.services.invoice_import import _apply_business_duplicate, _hard_duplicate
from domains.finance.services.account_resolver import load_postable_account
from domains.finance.services.posting_suggestions import resolve_confirm_posting_inputs
from domains.purchasing.services.expense_lines import (
    LINE_ALLOCATION_MISMATCH,
    MIXED_LINE_ACCOUNTS,
    LineAllocationMismatch,
    allocated_rows_match_header,
    parsed_ocr_lines_from_amounts,
    persist_extracted_lines,
)
from expenses.models import (
    Expense,
    ExpenseAttachment,
    ExpenseImportMetadata,
    ExpenseSource,
    IncomingInvoiceImport,
)
from expenses.services.numbering import assign_expense_number
from partners.models import Partner


def _resolve_confirm_posting_accounts(*, tenant, line_accounts, positions: set[int]) -> dict:
    if not positions:
        if line_accounts:
            raise PurchasingBadRequest('unknown_line', 'Stavka nije pronađena na ovom nalogu.')
        return {}
    mapping = {position: None for position in positions}
    seen = set()
    for item in line_accounts:
        position = int(item.get('position'))
        if position in seen:
            raise PurchasingBadRequest('duplicate_line', 'Ista stavka ne smije se slati dvaput.')
        seen.add(position)
        if position not in mapping:
            raise PurchasingBadRequest('unknown_line', 'Stavka nije pronađena na ovom nalogu.')
        account_id = item.get('posting_account_id')
        if account_id is None:
            mapping[position] = None
        else:
            mapping[position] = load_postable_account(
                tenant,
                int(account_id),
                field='line_accounts',
            )
    assigned = [account for account in mapping.values() if account is not None]
    if assigned and len(assigned) != len(positions):
        raise PurchasingBadRequest('mixed_line_accounts', MIXED_LINE_ACCOUNTS)
    return mapping


def _confirm_header_net_tax(values: dict) -> tuple[Decimal, Decimal]:
    tax = Decimal(values['tax_amount'] or 0)
    net = Decimal(values['amount']) - tax
    if net < 0:
        net = Decimal('0.00')
    return net, tax


def confirm_invoice_import(*, tenant, import_id: int, actor, data: dict | None = None) -> dict:
    payload = dict(data or {})
    override = bool(payload.pop('duplicate_override', False))
    direction_override = bool(payload.pop('direction_override', False))
    category_id = payload.pop('category_id', None)
    expense_account_id = payload.pop('expense_account_id', None)
    cost_center_id = payload.pop('cost_center_id', None)
    remember = bool(payload.pop('remember_category_for_partner', False))
    line_accounts = list(payload.pop('line_accounts', None) or [])

    with transaction.atomic():
        run = (
            IncomingInvoiceImport.all_objects.select_for_update(of=('self',))
            .filter(tenant=tenant, pk=import_id)
            .first()
        )
        if run is None:
            raise Http404()
        if run.status == IncomingInvoiceImport.STATUS_CONFIRMED and run.confirmed_expense_id:
            return import_dto(run)
        if run.status != IncomingInvoiceImport.STATUS_EXTRACTED:
            raise InvalidImportStatus()

        extracted = run.extracted_payload or {}
        identity = load_company_identity(tenant)
        direction = classify_document(extracted, identity)
        if direction_unresolved(direction, extracted):
            raise DirectionUnresolved()
        if direction_override_required(direction, override=direction_override):
            raise DirectionOverrideRequired()

        hard = _hard_duplicate(tenant=tenant, digest=run.file_sha256, exclude_id=run.pk)
        if hard is not None or run.duplicate_kind == IncomingInvoiceImport.DUPLICATE_HARD:
            if hard is not None:
                run.duplicate_kind = IncomingInvoiceImport.DUPLICATE_HARD
                run.duplicate_expense_id = hard.confirmed_expense_id
                run.save(update_fields=['duplicate_kind', 'duplicate_expense', 'updated_at'])
            raise HardDuplicate()

        if run.matched_partner_id is None:
            raise PartnerRequired()

        _apply_business_duplicate(run, run.extracted_payload or {})
        if run.duplicate_kind == IncomingInvoiceImport.DUPLICATE_BUSINESS and not override:
            run.save(update_fields=['duplicate_kind', 'duplicate_expense', 'duplicate_detail', 'updated_at'])
            raise DuplicateOverrideRequired()

        values = confirm_values(run.extracted_payload or {}, payload)
        if not values['invoice_number'] or values['issue_date'] is None or values['amount'] is None:
            raise ValidationError({'detail': 'Broj računa, datum i iznos su obavezni.'})
        if values['amount'] <= 0:
            raise ValidationError({'detail': 'Iznos mora biti veći od nule.'})

        extracted_payload = run.extracted_payload or {}
        header_net, header_tax = _confirm_header_net_tax(values)
        preview_rows = parsed_ocr_lines_from_amounts(
            extracted_payload,
            header_net=header_net,
            header_tax=header_tax,
        )
        if preview_rows and not allocated_rows_match_header(
            preview_rows,
            header_net=header_net,
            header_tax=header_tax,
        ):
            raise PurchasingBadRequest('line_allocation_mismatch', LINE_ALLOCATION_MISMATCH)
        posting_accounts = _resolve_confirm_posting_accounts(
            tenant=tenant,
            line_accounts=line_accounts,
            positions={row['position'] for row in preview_rows},
        )

        lock_open_vat_period_for_source_mutation(tenant, values['issue_date'])

        partner = Partner.all_objects.select_for_update().get(pk=run.matched_partner_id)
        if is_hard_own_company(
            {
                'name': partner.name,
                'oib': partner.tax_number,
                'vat_number': partner.vat_number,
            },
            identity,
        ):
            raise OwnCompanySupplier()
        if partner.partner_type == 'customer':
            partner.partner_type = 'both'
            partner.save(update_fields=['partner_type'])

        category, expense_account, account_source = resolve_confirm_posting_inputs(
            tenant=tenant,
            partner=partner,
            category_id=category_id,
            expense_account_id=expense_account_id,
            remember_category_for_partner=remember,
        )
        cost_center = None
        if cost_center_id not in (None, ''):
            from domains.finance.services.cost_center_resolver import load_bookable_cost_center

            cost_center = load_bookable_cost_center(tenant, int(cost_center_id))

        expense = Expense.all_objects.create(
            tenant=tenant,
            expense_number=assign_expense_number(tenant, year=values['issue_date'].year),
            source=ExpenseSource.OCR,
            status='draft',
            category=category,
            expense_account=expense_account,
            expense_account_source=account_source,
            cost_center=cost_center,
            supplier=partner,
            amount=values['amount'],
            tax_amount=values['tax_amount'] or 0,
            currency=values['currency'],
            expense_date=values['issue_date'],
            due_date=values['due_date'],
            receipt_number=values['invoice_number'],
            description=values['description'],
            created_by=actor,
        )
        try:
            persist_extracted_lines(
                expense=expense,
                payload=extracted_payload,
                posting_accounts=posting_accounts,
            )
        except LineAllocationMismatch as exc:
            raise PurchasingBadRequest(exc.code, exc.detail) from exc
        ExpenseImportMetadata.all_objects.create(
            tenant=tenant,
            expense=expense,
            source=ExpenseSource.OCR,
            external_id=f'ocr:{run.import_uuid}',
            raw_payload={
                'import_id': run.pk,
                'extracted': run.extracted_payload,
                'confirmed': {
                    'invoice_number': values['invoice_number'],
                    'issue_date': str(values['issue_date']),
                    'amount': str(values['amount']),
                },
            },
        )
        original = run.original_file
        original.open('rb')
        try:
            content = original.read()
        finally:
            original.close()
        attachment = ExpenseAttachment(
            tenant=tenant,
            expense=expense,
            uploaded_by=actor,
            original_filename=run.original_filename,
        )
        attachment.file.save(run.original_filename, ContentFile(content), save=True)

        now = timezone.now()
        run.status = IncomingInvoiceImport.STATUS_CONFIRMED
        run.confirmed_expense = expense
        run.confirmed_by = actor
        run.confirmed_at = now
        run.duplicate_override = override
        run.finished_at = now
        run.save(
            update_fields=[
                'status',
                'confirmed_expense',
                'confirmed_by',
                'confirmed_at',
                'duplicate_override',
                'finished_at',
                'updated_at',
            ]
        )
        return import_dto(run)
