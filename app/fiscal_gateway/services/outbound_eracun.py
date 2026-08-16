from __future__ import annotations

import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from fiscal_gateway.client.as4_client import As4SendError, submit_invoice_xml_via_domibus
from fiscal_gateway.client.domibus_status import fetch_message_status, map_domibus_status
from fiscal_gateway.models import As4DocumentLink, DirectTenantConfig
from integrations.audit import log_audit_step
from integrations.models import IntegrationAuditLog
from invoices.models import Invoice


class DirectEracunError(Exception):
    pass


def _resolve_domibus_ws_url(config: DirectTenantConfig) -> str:
    return (config.domibus_ws_url or '').strip() or getattr(settings, 'DOMIBUS_WS_URL', '')


def _resolve_mps_url(config: DirectTenantConfig) -> str | None:
    url = (config.mps_service_url or '').strip()
    return url or getattr(settings, 'MPS_SERVICE_URL', None)


def _resolve_ap_party_id(config: DirectTenantConfig) -> str:
    return (config.ap_party_id or '').strip() or getattr(settings, 'DOMIBUS_AP_PARTY_ID', 'FISKAL 2')


@transaction.atomic
def send_outbound_invoice(
    config: DirectTenantConfig,
    invoice: Invoice,
    *,
    ubl_xml: str,
    correlation_id=None,
) -> As4DocumentLink:
    if not invoice.invoice_number:
        invoice.assign_invoice_number()
        invoice.save(update_fields=['invoice_number'])

    existing = As4DocumentLink.all_objects.filter(
        tenant=config.tenant,
        direction=As4DocumentLink.DIRECTION_OUTBOUND,
        content_type=ContentType.objects.get_for_model(Invoice),
        object_id=invoice.pk,
    ).first()
    if existing and existing.message_id:
        raise DirectEracunError('Račun je već poslan putem AS4')

    try:
        result = submit_invoice_xml_via_domibus(
            ubl_xml,
            initiator_ap_party_id=_resolve_ap_party_id(config),
            domibus_ws_url=_resolve_domibus_ws_url(config) or None,
            mps_base=_resolve_mps_url(config),
        )
    except (As4SendError,) as exc:
        if correlation_id:
            log_audit_step(
                tenant=config.tenant,
                step=IntegrationAuditLog.STEP_OUTBOUND_FAILED,
                status=IntegrationAuditLog.STATUS_FAILED,
                correlation_id=correlation_id,
                invoice=invoice,
                integration_type='eracun',
                provider='direct',
                detail={'messages': [str(exc)]},
            )
        raise DirectEracunError(str(exc)) from exc

    link = As4DocumentLink.all_objects.create(
        tenant=config.tenant,
        direction=As4DocumentLink.DIRECTION_OUTBOUND,
        message_id=result.message_id,
        as4_status=As4DocumentLink.STATUS_SENT,
        ubl_xml=ubl_xml,
        recipient_oib=result.customer_oib,
        supplier_oib=result.supplier_oib,
        content_type=ContentType.objects.get_for_model(Invoice),
        object_id=invoice.pk,
    )

    invoice.status = 'sent'
    invoice.save(update_fields=['status', 'updated_at'])
    config.last_outbound_at = timezone.now()
    config.save(update_fields=['last_outbound_at'])

    if correlation_id:
        log_audit_step(
            tenant=config.tenant,
            step=IntegrationAuditLog.STEP_OUTBOUND_SENT,
            status=IntegrationAuditLog.STATUS_SUCCESS,
            correlation_id=correlation_id,
            invoice=invoice,
            integration_type='eracun',
            provider='direct',
            detail={'message_id': result.message_id},
            as4_link=link,
        )

    return link


def poll_outbound_statuses(config: DirectTenantConfig) -> int:
    """Sync AS4 outbound delivery status from Domibus message log."""
    links = As4DocumentLink.all_objects.filter(
        tenant=config.tenant,
        direction=As4DocumentLink.DIRECTION_OUTBOUND,
        as4_status=As4DocumentLink.STATUS_SENT,
    ).exclude(message_id='')

    ws_url = _resolve_domibus_ws_url(config) or None
    updated = 0
    for link in links:
        raw = fetch_message_status(link.message_id, domibus_ws_url=ws_url)
        if not raw:
            continue
        mapped = map_domibus_status(raw)
        if mapped is None or mapped == link.as4_status:
            continue
        link.as4_status = mapped
        link.save(update_fields=['as4_status', 'last_synced_at'])
        updated += 1

        if mapped == As4DocumentLink.STATUS_DELIVERED and link.content_type.model == 'invoice':
            invoice = Invoice.all_objects.filter(pk=link.object_id).first()
            if invoice and invoice.status == 'sent':
                invoice.save(update_fields=['updated_at'])

    if updated:
        config.last_outbound_at = timezone.now()
        config.save(update_fields=['last_outbound_at'])
    return updated
