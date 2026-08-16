# Private API — vanjski pozivi idu preko integrations.IntegrationManager.
from __future__ import annotations

import base64
import os
from datetime import timedelta

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
from super_integration.ubl.parser import parse_invoice_ubl


def _system_user():
    User = get_user_model()
    return User.objects.filter(is_superuser=True).first() or User.objects.first()


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
    parsed = parse_invoice_ubl(ubl_b64)
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
        expense_date=parsed.issue_date or timezone.now().date(),
        due_date=parsed.due_date,
        amount=parsed.total_amount,
        tax_amount=parsed.tax_amount,
        currency=parsed.currency or 'EUR',
        payment_method='',
        description='; '.join(parsed.line_descriptions) or f'Ulazni eRačun {parsed.invoice_number}',
        raw_payload={
            'super_unique_id': str(summary.get('UniqueId') or ''),
            'super_status': summary.get('InvoiceStatus'),
            'supplier_address': parsed.supplier_address,
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
        super_unique_id=str(summary.get('UniqueId') or ''),
        super_status=summary.get('InvoiceStatus'),
        ubl_xml=ubl_xml,
        pdf_path=pdf_path,
        content_type=ContentType.objects.get_for_model(Expense),
        object_id=expense.pk,
    )
    return expense


def sync_inbound_invoices(config: SuperTenantConfig) -> int:
    client = SuperClient(config)
    received_from = None
    if config.last_inbound_sync_at:
        received_from = (config.last_inbound_sync_at - timedelta(days=1)).date().isoformat()

    summaries = client.get_invoice_list(received_date_from=received_from)
    user = _system_user()
    batch = ImportBatch.all_objects.create(
        tenant=config.tenant,
        source=ExpenseSource.SUPER,
        filename='',
        uploaded_by=user,
        dry_run=False,
    )
    created = 0
    for summary in summaries:
        expense = import_inbound_invoice(config, summary, client=client, batch=batch)
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
