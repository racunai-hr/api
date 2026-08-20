from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation
from domains.purchasing.services.dto import confirm_values, import_dto
from domains.purchasing.services.exceptions import (
    DuplicateOverrideRequired,
    HardDuplicate,
    InvalidImportStatus,
    PartnerRequired,
)
from domains.purchasing.services.invoice_import import _apply_business_duplicate, _hard_duplicate
from expenses.models import (
    Expense,
    ExpenseAttachment,
    ExpenseCategory,
    ExpenseImportMetadata,
    ExpenseSource,
    IncomingInvoiceImport,
)
from expenses.services.numbering import assign_expense_number
from partners.models import Partner


def _default_category(tenant) -> ExpenseCategory:
    category, _ = ExpenseCategory.all_objects.get_or_create(
        tenant=tenant,
        name='Ostalo',
        defaults={'description': 'Ostali troškovi'},
    )
    return category


def confirm_invoice_import(*, tenant, import_id: int, actor, data: dict | None = None) -> dict:
    payload = dict(data or {})
    override = bool(payload.pop('duplicate_override', False))

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

        lock_open_vat_period_for_source_mutation(tenant, values['issue_date'])

        partner = Partner.all_objects.select_for_update().get(pk=run.matched_partner_id)
        if partner.partner_type == 'customer':
            partner.partner_type = 'both'
            partner.save(update_fields=['partner_type'])

        expense = Expense.all_objects.create(
            tenant=tenant,
            expense_number=assign_expense_number(tenant, year=values['issue_date'].year),
            source=ExpenseSource.OCR,
            status='draft',
            category=_default_category(tenant),
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
