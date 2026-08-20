from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone

from domains.partners.services.partners import (
    PartnerFieldError,
    PartnerIbanConflict,
    PartnerTaxNumberConflict,
    PartnerVatNumberConflict,
    create_bank_account,
    create_partner,
    update_partner,
)
from domains.purchasing.ai.extraction import (
    ExtractionError,
    ExtractionTimeout,
    InvalidExtraction,
    get_extraction_provider,
)
from domains.purchasing.ai.render import RenderError, render_invoice_pages
from domains.purchasing.ai.schema import OCR_SCHEMA_VERSION
from domains.purchasing.services.dto import import_dto
from domains.purchasing.services.exceptions import (
    IdempotencyKeyReused,
    ImportProcessing,
    InvalidImportStatus,
    PartnerChanged,
    PartnerRequired,
    PurchasingBadRequest,
)
from domains.purchasing.services.matching import (
    compute_partner_diff,
    match_partner,
    parse_iso_date,
    parse_money,
    require_country_code,
    supplier_from_payload,
)
from shared.iban import normalize_iban
from shared.vat import normalize_vat_number
from expenses.models import Expense, IncomingInvoiceImport
from expenses.validators import MAX_ATTACHMENT_SIZE, detect_invoice_kind
from partners.models import Partner, PartnerBankAccount

logger = logging.getLogger(__name__)


class IdempotencyConflict(IdempotencyKeyReused):
    pass


@dataclass(frozen=True)
class CreateImportOutcome:
    run: IncomingInvoiceImport
    created: bool


def _sanitize_filename(name: str) -> str:
    base = os.path.basename(name or '') or 'racun.bin'
    cleaned = re.sub(r'[^\w.\-]+', '_', base, flags=re.UNICODE).strip('._')
    return (cleaned or 'racun.bin')[:200]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _content_type_for(kind: str | None) -> str:
    return {
        'pdf': 'application/pdf',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
    }.get(kind or '', 'application/octet-stream')


def _get_import(tenant, import_id: int) -> IncomingInvoiceImport:
    run = IncomingInvoiceImport.all_objects.filter(tenant=tenant, pk=import_id).first()
    if run is None:
        raise Http404()
    return run


def _hard_duplicate(*, tenant, digest: str, exclude_id: int | None = None):
    qs = IncomingInvoiceImport.all_objects.filter(
        tenant=tenant,
        file_sha256=digest,
        status=IncomingInvoiceImport.STATUS_CONFIRMED,
    )
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    return qs.select_related('confirmed_expense', 'confirmed_expense__supplier').first()


def _apply_hard_duplicate(run: IncomingInvoiceImport, existing: IncomingInvoiceImport | None) -> None:
    if existing is None:
        return
    run.duplicate_kind = IncomingInvoiceImport.DUPLICATE_HARD
    run.duplicate_expense_id = existing.confirmed_expense_id
    run.duplicate_detail = {
        'import_id': existing.pk,
        'expense_id': existing.confirmed_expense_id,
    }


def _apply_business_duplicate(run: IncomingInvoiceImport, payload: dict) -> None:
    if run.duplicate_kind == IncomingInvoiceImport.DUPLICATE_HARD:
        return
    supplier = supplier_from_payload(payload)
    invoice_number = str(payload.get('invoice_number') or '').strip()
    issue_date = parse_iso_date(payload.get('issue_date'))
    total = parse_money(payload.get('total_amount'))
    if not (supplier.get('oib') and invoice_number and issue_date and total):
        return
    expense = (
        Expense.all_objects.filter(
            tenant=run.tenant,
            supplier__tax_number=supplier['oib'],
            receipt_number=invoice_number,
            expense_date=issue_date,
            amount=total,
        )
        .select_related('supplier')
        .first()
    )
    if expense is None:
        return
    run.duplicate_kind = IncomingInvoiceImport.DUPLICATE_BUSINESS
    run.duplicate_expense = expense
    run.duplicate_detail = {
        'expense_id': expense.pk,
        'invoice_number': invoice_number,
        'issue_date': str(issue_date),
        'total': str(total),
    }


def _extraction_warnings(payload: dict) -> list[str]:
    warnings: list[str] = []
    supplier = supplier_from_payload(payload)
    if not supplier.get('oib'):
        warnings.append('Porezni identifikator dobavljača nije pronađen.')
    raw_country = ''
    raw_supplier = payload.get('supplier') if isinstance(payload.get('supplier'), dict) else {}
    raw_country = str(raw_supplier.get('country') or '').strip()
    if (supplier.get('country') or raw_country) and not supplier.get('country_code'):
        warnings.append(
            'Država dobavljača nije mapirana na ISO kod — odaberite je prije kreiranja partnera.'
        )
    if not str(payload.get('invoice_number') or '').strip():
        warnings.append('Broj računa nije pronađen.')
    if parse_iso_date(payload.get('issue_date')) is None:
        warnings.append('Datum računa nije pronađen.')
    net = parse_money(payload.get('net_amount'))
    tax = parse_money(payload.get('tax_amount'))
    total = parse_money(payload.get('total_amount'))
    if total is None:
        warnings.append('Ukupan iznos nije pronađen.')
    elif net is not None and tax is not None:
        if abs((net + tax) - total) > Decimal('0.05'):
            warnings.append('Osnovica + PDV ne odgovaraju ukupnom iznosu.')
    warnings.extend(str(item) for item in (payload.get('warnings') or []) if item)
    return warnings


def _apply_partner_match(run: IncomingInvoiceImport, payload: dict) -> None:
    result = match_partner(tenant=run.tenant, payload=payload)
    run.matched_partner = result['partner']
    run.partner_match = result['kind']
    run.partner_candidate_id = result['candidate_id']
    run.partner_diff = result['diff']


def create_invoice_import(
    *,
    tenant,
    actor,
    content: bytes,
    filename: str,
    idempotency_key: str,
) -> CreateImportOutcome:
    key = (idempotency_key or '').strip()
    if not key:
        raise ValidationError({'Idempotency-Key': 'Header Idempotency-Key je obavezan.'})
    if not content:
        raise ValidationError({'file': 'Datoteka je prazna.'})
    if len(content) > MAX_ATTACHMENT_SIZE:
        raise ValidationError({'file': 'Datoteka je veća od 10 MB.'})
    kind = detect_invoice_kind(content[:8])
    if kind is None:
        raise ValidationError({'file': 'Datoteka mora biti PDF, JPG ili PNG.'})

    digest = _sha256(content)
    safe_name = _sanitize_filename(filename)
    existing = (
        IncomingInvoiceImport.all_objects.filter(tenant=tenant, idempotency_key=key)
        .order_by('-created_at')
        .first()
    )
    if existing is not None:
        if existing.file_sha256 != digest:
            raise IdempotencyKeyReused()
        return CreateImportOutcome(run=existing, created=False)

    run = IncomingInvoiceImport(
        tenant=tenant,
        uploaded_by=actor if getattr(actor, 'is_authenticated', False) else None,
        original_filename=safe_name,
        content_type=_content_type_for(kind),
        file_sha256=digest,
        file_size=len(content),
        status=IncomingInvoiceImport.STATUS_QUEUED,
        idempotency_key=key,
    )
    _apply_hard_duplicate(run, _hard_duplicate(tenant=tenant, digest=digest))
    run.original_file.save(safe_name, ContentFile(content), save=False)
    try:
        run.save()
    except IntegrityError as exc:
        raced = IncomingInvoiceImport.all_objects.filter(tenant=tenant, idempotency_key=key).first()
        if raced is None:
            raise
        if raced.file_sha256 != digest:
            raise IdempotencyKeyReused() from exc
        return CreateImportOutcome(run=raced, created=False)
    return CreateImportOutcome(run=run, created=True)


def enqueue_invoice_import(run_id: int) -> IncomingInvoiceImport:
    from expenses.tasks import execute_incoming_invoice_import_task

    run = IncomingInvoiceImport.all_objects.get(pk=run_id)
    if run.status != IncomingInvoiceImport.STATUS_QUEUED:
        return run
    async_result = execute_incoming_invoice_import_task.delay(run.pk)
    IncomingInvoiceImport.all_objects.filter(
        pk=run.pk,
        status=IncomingInvoiceImport.STATUS_QUEUED,
    ).update(celery_task_id=async_result.id or '')
    run.refresh_from_db()
    return run


def submit_invoice_import(*, tenant, actor, content, filename, idempotency_key) -> tuple[dict, int, bool]:
    outcome = create_invoice_import(
        tenant=tenant,
        actor=actor,
        content=content,
        filename=filename,
        idempotency_key=idempotency_key,
    )
    run = outcome.run
    if run.status == IncomingInvoiceImport.STATUS_QUEUED and (outcome.created or not run.celery_task_id):
        enqueue_invoice_import(run.pk)
        run.refresh_from_db()
    payload = import_dto(run, created=outcome.created)
    return payload, 202, outcome.created


def _claim_queued(run_id: int) -> IncomingInvoiceImport | None:
    now = timezone.now()
    updated = IncomingInvoiceImport.all_objects.filter(
        pk=run_id,
        status=IncomingInvoiceImport.STATUS_QUEUED,
    ).update(status=IncomingInvoiceImport.STATUS_PROCESSING, started_at=now, last_error='')
    if updated != 1:
        return None
    return IncomingInvoiceImport.all_objects.select_related('tenant', 'matched_partner').get(pk=run_id)


def _fail_processing(run_id: int, message: str) -> IncomingInvoiceImport | None:
    now = timezone.now()
    updated = IncomingInvoiceImport.all_objects.filter(
        pk=run_id,
        status=IncomingInvoiceImport.STATUS_PROCESSING,
    ).update(
        status=IncomingInvoiceImport.STATUS_FAILED,
        last_error=message[:500],
        finished_at=now,
    )
    if updated != 1:
        return IncomingInvoiceImport.all_objects.filter(pk=run_id).first()
    return IncomingInvoiceImport.all_objects.get(pk=run_id)


def execute_invoice_import(run_id: int) -> IncomingInvoiceImport:
    run = _claim_queued(run_id)
    if run is None:
        return IncomingInvoiceImport.all_objects.get(pk=run_id)
    try:
        content = run.original_file.read()
        pages = render_invoice_pages(content)
        result = get_extraction_provider().extract(pages, filename=run.original_filename)
    except ExtractionTimeout as exc:
        return _fail_processing(run_id, str(exc) or 'OpenAI timeout')
    except InvalidExtraction as exc:
        return _fail_processing(run_id, str(exc) or 'Nevaljani Structured Output')
    except (ExtractionError, RenderError, OSError, ValueError) as exc:
        logger.exception('OCR extraction failed for import %s', run_id)
        return _fail_processing(run_id, str(exc)[:500] or exc.__class__.__name__)

    payload = result.payload
    warnings = _extraction_warnings(payload)
    now = timezone.now()
    with transaction.atomic():
        locked = (
            IncomingInvoiceImport.all_objects.select_for_update(of=('self',))
            .filter(pk=run_id, status=IncomingInvoiceImport.STATUS_PROCESSING)
            .first()
        )
        if locked is None:
            return IncomingInvoiceImport.all_objects.get(pk=run_id)
        locked.extracted_payload = payload
        locked.warnings = warnings
        locked.ocr_provider = result.provider
        locked.ocr_model = result.model
        locked.ocr_schema_version = result.schema_version or OCR_SCHEMA_VERSION
        locked.ocr_extracted_at = now
        _apply_partner_match(locked, payload)
        if locked.duplicate_kind != IncomingInvoiceImport.DUPLICATE_HARD:
            _apply_hard_duplicate(
                locked,
                _hard_duplicate(tenant=locked.tenant, digest=locked.file_sha256, exclude_id=locked.pk),
            )
        _apply_business_duplicate(locked, payload)
        locked.status = IncomingInvoiceImport.STATUS_EXTRACTED
        locked.finished_at = now
        locked.last_error = ''
        locked.save()
        return locked


def retry_invoice_import(*, tenant, import_id: int) -> dict:
    run = _get_import(tenant, import_id)
    if run.status != IncomingInvoiceImport.STATUS_FAILED:
        raise InvalidImportStatus('Ponovni pokušaj je moguć samo nakon neuspjeha.')
    IncomingInvoiceImport.all_objects.filter(pk=run.pk, status=IncomingInvoiceImport.STATUS_FAILED).update(
        status=IncomingInvoiceImport.STATUS_QUEUED,
        celery_task_id='',
        last_error='',
        started_at=None,
        finished_at=None,
    )
    enqueue_invoice_import(run.pk)
    run.refresh_from_db()
    return import_dto(run)


def discard_invoice_import(*, tenant, import_id: int) -> dict:
    run = _get_import(tenant, import_id)
    if run.status == IncomingInvoiceImport.STATUS_PROCESSING:
        raise ImportProcessing()
    if run.status not in (
        IncomingInvoiceImport.STATUS_QUEUED,
        IncomingInvoiceImport.STATUS_EXTRACTED,
    ):
        raise InvalidImportStatus()
    now = timezone.now()
    updated = IncomingInvoiceImport.all_objects.filter(
        pk=run.pk,
        status__in=(IncomingInvoiceImport.STATUS_QUEUED, IncomingInvoiceImport.STATUS_EXTRACTED),
    ).update(status=IncomingInvoiceImport.STATUS_DISCARDED, finished_at=now)
    if updated != 1:
        run.refresh_from_db()
        if run.status == IncomingInvoiceImport.STATUS_PROCESSING:
            raise ImportProcessing()
        raise InvalidImportStatus()
    run.refresh_from_db()
    return import_dto(run)


def _require_extracted(run: IncomingInvoiceImport) -> None:
    if run.status != IncomingInvoiceImport.STATUS_EXTRACTED:
        raise InvalidImportStatus()


def create_partner_from_import(*, tenant, import_id: int, data: dict, user=None) -> dict:
    with transaction.atomic():
        from tenants.models import Tenant

        Tenant.objects.select_for_update().get(pk=tenant.pk)
        run = (
            IncomingInvoiceImport.all_objects.select_for_update(of=('self',))
            .filter(tenant=tenant, pk=import_id)
            .first()
        )
        if run is None:
            raise Http404()
        _require_extracted(run)
        payload = dict(data or {})
        payload.setdefault('partner_type', 'supplier')
        legacy_country = str(payload.pop('country', '') or '')
        payload['country_code'] = require_country_code(
            country_code=str(payload.get('country_code') or ''),
            country=legacy_country,
        )
        payload['vat_number'] = normalize_vat_number(str(payload.get('vat_number') or ''))
        payload['tax_number'] = str(payload.get('tax_number') or '').strip()
        oib = payload['tax_number']
        vat = payload['vat_number']

        existing = None
        match_kind = IncomingInvoiceImport.MATCH_EXACT_OIB
        if oib:
            existing = Partner.all_objects.select_for_update().filter(tenant=tenant, tax_number=oib).first()
            match_kind = IncomingInvoiceImport.MATCH_EXACT_OIB
        if existing is None and vat:
            existing = (
                Partner.all_objects.select_for_update()
                .filter(tenant=tenant, vat_number=vat)
                .exclude(vat_number='')
                .first()
            )
            match_kind = IncomingInvoiceImport.MATCH_VAT
        if existing is not None:
            if existing.partner_type == 'customer':
                update_partner(tenant, existing.pk, {'partner_type': 'both'})
                existing.refresh_from_db()
            run.matched_partner = existing
            run.partner_match = match_kind
            run.partner_diff = compute_partner_diff(
                existing,
                supplier_from_payload(run.extracted_payload or {}),
            )
            run.save(update_fields=['matched_partner', 'partner_match', 'partner_diff', 'updated_at'])
            return import_dto(run)
        try:
            created = create_partner(tenant, payload, user=user)
        except (PartnerTaxNumberConflict, PartnerVatNumberConflict) as conflict:
            if isinstance(conflict, PartnerVatNumberConflict) and vat:
                existing = (
                    Partner.all_objects.filter(tenant=tenant, vat_number=vat)
                    .exclude(vat_number='')
                    .first()
                )
                match_kind = IncomingInvoiceImport.MATCH_VAT
            elif oib:
                existing = Partner.all_objects.filter(tenant=tenant, tax_number=oib).first()
                match_kind = IncomingInvoiceImport.MATCH_EXACT_OIB
            else:
                existing = None
            if existing is None:
                raise
            run.matched_partner_id = existing.pk
            run.partner_match = match_kind
            run.partner_diff = compute_partner_diff(
                existing,
                supplier_from_payload(run.extracted_payload or {}),
            )
            run.save(update_fields=['matched_partner', 'partner_match', 'partner_diff', 'updated_at'])
            return import_dto(run)
        except PartnerFieldError as exc:
            raise PurchasingBadRequest(exc.code, exc.detail) from exc
        partner = Partner.all_objects.get(pk=created['id'])
        iban = normalize_iban(str(payload.get('iban') or ''))
        if iban:
            try:
                create_bank_account(tenant, partner.pk, {'iban': iban, 'is_primary': True})
            except (PartnerIbanConflict, ValueError):
                pass
        run.matched_partner = partner
        run.partner_match = (
            IncomingInvoiceImport.MATCH_VAT if vat and not oib else IncomingInvoiceImport.MATCH_EXACT_OIB
        )
        run.partner_diff = compute_partner_diff(
            partner,
            supplier_from_payload(run.extracted_payload or {}),
        )
        run.save(update_fields=['matched_partner', 'partner_match', 'partner_diff', 'updated_at'])
        return import_dto(run)


def apply_partner_updates(*, tenant, import_id: int) -> dict:
    with transaction.atomic():
        run = (
            IncomingInvoiceImport.all_objects.select_for_update(of=('self',))
            .filter(tenant=tenant, pk=import_id)
            .first()
        )
        if run is None:
            raise Http404()
        _require_extracted(run)
        if run.matched_partner_id is None:
            raise PartnerRequired()
        partner = Partner.all_objects.select_for_update().get(pk=run.matched_partner_id)
        supplier = supplier_from_payload(run.extracted_payload or {})
        for field in ('address', 'city', 'postal_code'):
            extracted = (supplier.get(field) or '').strip()
            if not extracted:
                continue
            current = (getattr(partner, field) or '').strip()
            if current and current != extracted:
                run.partner_diff = compute_partner_diff(partner, supplier)
                run.save(update_fields=['partner_diff', 'updated_at'])
                raise PartnerChanged()
            if not current:
                update_partner(tenant, partner.pk, {field: extracted})
                partner.refresh_from_db()
        extracted_iban = supplier.get('iban') or ''
        if extracted_iban:
            existing_ibans = [
                normalize_iban(row.iban)
                for row in PartnerBankAccount.objects.filter(partner=partner)
            ]
            if extracted_iban not in existing_ibans:
                try:
                    create_bank_account(tenant, partner.pk, {'iban': extracted_iban, 'is_primary': not existing_ibans})
                except PartnerIbanConflict:
                    pass
                partner.refresh_from_db()
        run.partner_diff = compute_partner_diff(partner, supplier)
        run.save(update_fields=['partner_diff', 'updated_at'])
        return import_dto(run)


def get_invoice_import(*, tenant, import_id: int) -> dict:
    return import_dto(_get_import(tenant, import_id))
