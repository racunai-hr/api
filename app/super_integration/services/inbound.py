# Private API — vanjski pozivi idu preko integrations.IntegrationManager.
from __future__ import annotations

import base64
import os
from datetime import date, datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from expenses.models import Expense, ExpenseSource, ImportBatch
from expenses.parsers.types import NormalizedExpenseRow
from expenses.services.import_service import import_expense_rows
from super_integration.client import SuperAPIError, SuperClient
from super_integration.config_utils import SuperConfigError, get_active_super_config
from super_integration.models import SuperDocumentLink, SuperTenantConfig
from super_integration.ubl.parser import UblMonetaryError, parse_invoice_ubl


def _system_user():
    User = get_user_model()
    return User.objects.filter(is_superuser=True).first() or User.objects.first()


def _as_date(value) -> date | None:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@transaction.atomic
def import_inbound_invoice(
    config: SuperTenantConfig,
    summary: dict,
    client: SuperClient | None = None,
    *,
    batch: ImportBatch | None = None,
) -> Expense | None:
    client = client or SuperClient(config)
    guid = summary.get('Guid') or summary.get('guid')
    if not guid:
        return None

    if SuperDocumentLink.all_objects.filter(
        tenant=config.tenant,
        direction=SuperDocumentLink.DIRECTION_INBOUND,
        super_guid=guid,
    ).exists():
        return None

    ubl_b64, _meta = client.get_invoice_ubl(guid)
    try:
        parsed = parse_invoice_ubl(ubl_b64)
    except UblMonetaryError as exc:
        raise SuperAPIError(str(exc)) from exc
    user = _system_user()
    if not user:
        raise SuperAPIError('Nema korisnika za kreiranje troška')

    row = NormalizedExpenseRow(
        row_number=0,
        source=ExpenseSource.SUPER,
        external_id=guid,
        supplier_oib=parsed.supplier_oib or f'unknown-{guid[:8]}',
        supplier_name=parsed.supplier_name,
        receipt_number=parsed.invoice_number,
        expense_date=_as_date(parsed.issue_date) or timezone.now().date(),
        due_date=_as_date(parsed.due_date),
        amount=parsed.total_amount,
        tax_amount=parsed.tax_amount,
        currency=parsed.currency or 'EUR',
        payment_method='',
        description='; '.join(parsed.line_descriptions) or f'Ulazni eRačun {parsed.invoice_number}',
        raw_payload={
            'super_unique_id': str(summary.get('UniqueId') or summary.get('uniqueId') or ''),
            'super_status': summary.get('InvoiceStatus') or summary.get('invoiceStatus'),
            'supplier_address': parsed.supplier_address,
            # UBL SSOT for Expense.amount; list fields below are audit only.
            'payable_amount': str(parsed.payable_amount),
            'prepaid_amount': str(parsed.prepaid_amount),
            'super_list_total_amount': summary.get('totalAmount') or summary.get('TotalAmount'),
            'super_list_payable_amount': summary.get('payableAmount') or summary.get('PayableAmount'),
            'super_list_prepaid_amount': summary.get('prepaidAmount') or summary.get('PrepaidAmount'),
        },
        super_guid=guid,
        status='draft',
    )

    result = import_expense_rows(
        tenant=config.tenant,
        user=user,
        rows=[row],
        source=ExpenseSource.SUPER,
        filename='',
        dry_run=False,
        batch=batch,
    )
    if result.created == 0:
        return None

    expense = Expense.all_objects.filter(
        tenant=config.tenant,
        import_metadata__source=ExpenseSource.SUPER,
        import_metadata__external_id=guid,
    ).first()
    if not expense:
        return None

    pdf_b64 = ''
    try:
        pdf_b64 = client.get_invoice_visualization_pdf_b64(guid)
    except SuperAPIError:
        pass

    pdf_path = ''
    if pdf_b64:
        media_root = getattr(settings, 'MEDIA_ROOT', 'media')
        rel_dir = os.path.join('expenses', 'super', str(config.tenant_id))
        abs_dir = os.path.join(media_root, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)
        filename = f'{guid}.pdf'
        abs_path = os.path.join(abs_dir, filename)
        with open(abs_path, 'wb') as handle:
            handle.write(base64.b64decode(pdf_b64))
        pdf_path = os.path.join(rel_dir, filename)

    ubl_xml = ubl_b64 if ubl_b64.strip().startswith('<') else base64.b64decode(ubl_b64).decode('utf-8', errors='replace')

    SuperDocumentLink.all_objects.create(
        tenant=config.tenant,
        direction=SuperDocumentLink.DIRECTION_INBOUND,
        super_guid=guid,
        super_unique_id=str(summary.get('UniqueId') or summary.get('uniqueId') or ''),
        super_status=summary.get('InvoiceStatus') or summary.get('invoiceStatus'),
        ubl_xml=ubl_xml,
        pdf_path=pdf_path,
        content_type=ContentType.objects.get_for_model(Expense),
        object_id=expense.pk,
    )
    return expense


def sync_inbound_invoices(config: SuperTenantConfig) -> int:
    client = SuperClient(config)
    unique_from = 1
    last_link = (
        SuperDocumentLink.all_objects.filter(
            tenant=config.tenant,
            direction=SuperDocumentLink.DIRECTION_INBOUND,
        )
        .exclude(super_unique_id='')
        .order_by('-id')
        .first()
    )
    if last_link and str(last_link.super_unique_id).isdigit():
        unique_from = max(1, int(last_link.super_unique_id) - 50)

    summaries = client.get_invoice_list(unique_from=unique_from)
    user = _system_user()
    batch = ImportBatch.all_objects.create(
        tenant=config.tenant,
        source=ExpenseSource.SUPER,
        filename='',
        uploaded_by=user,
        dry_run=False,
    )
    created = 0
    errors = 0
    for summary in summaries:
        guid = summary.get('Guid') or summary.get('guid')
        try:
            expense = import_inbound_invoice(config, summary, client=client, batch=batch)
        except Exception:
            errors += 1
            continue
        if expense:
            created += 1
    client.touch_inbound_sync()
    return created


def approve_inbound_expense(expense: Expense, user=None) -> SuperDocumentLink:
    link = SuperDocumentLink.all_objects.filter(
        tenant=expense.tenant,
        direction=SuperDocumentLink.DIRECTION_INBOUND,
        content_type=ContentType.objects.get_for_model(Expense),
        object_id=expense.pk,
    ).first()
    if not link:
        raise SuperAPIError('Trošak nije povezan sa SUPER ulaznim računom')

    try:
        config = get_active_super_config(expense.tenant)
    except SuperConfigError as exc:
        raise SuperAPIError(str(exc)) from exc

    client = SuperClient(config)
    client.set_invoice_status(link.super_guid, SuperClient.INBOUND_STATUS_APPROVED)
    link.super_status = SuperClient.INBOUND_STATUS_APPROVED
    link.save(update_fields=['super_status', 'last_synced_at'])

    expense.status = 'approved'
    expense.approved_by = user
    expense.save(update_fields=['status', 'approved_by', 'updated_at'])
    return link


def reject_inbound_expense(expense: Expense, description: str = '') -> SuperDocumentLink:
    link = SuperDocumentLink.all_objects.filter(
        tenant=expense.tenant,
        direction=SuperDocumentLink.DIRECTION_INBOUND,
        content_type=ContentType.objects.get_for_model(Expense),
        object_id=expense.pk,
    ).first()
    if not link:
        raise SuperAPIError('Trošak nije povezan sa SUPER ulaznim računom')

    try:
        config = get_active_super_config(expense.tenant)
    except SuperConfigError as exc:
        raise SuperAPIError(str(exc)) from exc

    client = SuperClient(config)
    client.reject_invoice(link.super_guid, description=description)
    link.super_status = SuperClient.INBOUND_STATUS_REJECTED
    link.save(update_fields=['super_status', 'last_synced_at'])

    expense.status = 'rejected'
    expense.save(update_fields=['status', 'updated_at'])
    return link
