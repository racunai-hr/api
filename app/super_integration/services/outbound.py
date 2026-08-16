# Private API — vanjski pozivi idu preko integrations.InvoiceIntegrationService / IntegrationManager.
from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from invoices.models import Invoice
from settings.models import CompanySettings
from super_integration.client import SuperAPIError, SuperClient
from super_integration.config_utils import SuperConfigError, get_active_super_config
from super_integration.models import SuperDocumentLink, SuperTenantConfig

from integrations.audit import log_audit_step
from integrations.models import IntegrationAuditLog


def _get_config(tenant) -> SuperTenantConfig:
    try:
        return get_active_super_config(tenant)
    except SuperConfigError as exc:
        raise SuperAPIError(str(exc)) from exc


@transaction.atomic
def send_outbound_invoice(invoice: Invoice, *, ubl_xml: str, correlation_id=None) -> SuperDocumentLink:
    if not invoice.invoice_number:
        invoice.assign_invoice_number()
        invoice.save(update_fields=['invoice_number'])

    if invoice.items.count() == 0:
        raise SuperAPIError('Račun mora imati barem jednu stavku')

    existing = SuperDocumentLink.all_objects.filter(
        tenant=invoice.tenant,
        direction=SuperDocumentLink.DIRECTION_OUTBOUND,
        content_type=ContentType.objects.get_for_model(Invoice),
        object_id=invoice.pk,
    ).first()
    if existing and existing.super_guid:
        raise SuperAPIError('Račun je već poslan u SUPER')

    company = CompanySettings.all_objects.filter(tenant=invoice.tenant).first()
    if not company or not company.vat_number:
        raise SuperAPIError('Postavke tvrtke / OIB nisu popunjene')

    partner = invoice.company_to
    if not partner.tax_number:
        raise SuperAPIError('Kupac nema OIB')

    config = _get_config(invoice.tenant)
    client = SuperClient(config)

    if not client.check_participant(partner.tax_number.replace('HR', '')):
        raise SuperAPIError(f'Kupac OIB {partner.tax_number} nije registriran na AMS-u')

    response = client.send_sending_invoice_ubl(ubl_xml)

    link = SuperDocumentLink.all_objects.create(
        tenant=invoice.tenant,
        direction=SuperDocumentLink.DIRECTION_OUTBOUND,
        super_guid=response.get('Guid') or response.get('guid') or '',
        super_unique_id=str(response.get('UniqueId') or response.get('uniqueId') or ''),
        super_status=SuperClient.OUTBOUND_STATUS_SENT,
        ubl_xml=ubl_xml,
        content_type=ContentType.objects.get_for_model(Invoice),
        object_id=invoice.pk,
    )

    invoice.status = 'sent'
    invoice.save(update_fields=['status', 'updated_at'])

    if correlation_id:
        log_audit_step(
            tenant=invoice.tenant,
            step=IntegrationAuditLog.STEP_OUTBOUND_SENT,
            status=IntegrationAuditLog.STATUS_SUCCESS,
            correlation_id=correlation_id,
            invoice=invoice,
            integration_type='eracun',
            provider='super',
            detail={'super_guid': link.super_guid},
            super_link=link,
        )

    return link


def poll_outbound_statuses(config: SuperTenantConfig) -> int:
    client = SuperClient(config)
    links = SuperDocumentLink.all_objects.filter(
        tenant=config.tenant,
        direction=SuperDocumentLink.DIRECTION_OUTBOUND,
    ).exclude(super_guid='')

    guids = [link.super_guid for link in links if link.super_guid]
    if not guids:
        return 0

    statuses = client.get_sending_invoice_statuses(guids)
    updated = 0
    status_map = {item.get('Guid'): item for item in statuses}

    for link in links:
        item = status_map.get(link.super_guid)
        if not item:
            continue
        status = item.get('SendingInvoiceStatus') or item.get('invoiceStatus')
        if status is None:
            continue
        link.super_status = int(status)
        link.status_remark = item.get('StatusRemark') or item.get('RejectedRemark') or ''
        link.save(update_fields=['super_status', 'status_remark', 'last_synced_at'])

        if link.content_type.model == 'invoice':
            invoice = Invoice.all_objects.filter(pk=link.object_id).first()
            if invoice and int(status) >= SuperClient.OUTBOUND_STATUS_DELIVERED and invoice.status == 'sent':
                invoice.status = 'sent'
                invoice.save(update_fields=['status', 'updated_at'])
        updated += 1

    client.touch_outbound_poll()
    return updated


def report_invoice_payment(invoice: Invoice) -> dict | None:
    link = SuperDocumentLink.all_objects.filter(
        tenant=invoice.tenant,
        direction=SuperDocumentLink.DIRECTION_OUTBOUND,
        content_type=ContentType.objects.get_for_model(Invoice),
        object_id=invoice.pk,
    ).first()
    if not link or not link.super_guid:
        return None

    config = _get_config(invoice.tenant)
    client = SuperClient(config)
    return client.add_payment_for_sending_invoice(
        link.super_guid,
        invoice.issue_date,
        invoice.total_amount,
    )
