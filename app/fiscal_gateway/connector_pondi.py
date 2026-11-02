from __future__ import annotations

import uuid

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from fiscal_gateway.client.gateway_v1_client import GatewayV1Client, GatewayV1Error
from integrations.audit import log_audit_step
from integrations.constants import IntegrationProvider, IntegrationType
from integrations.models import IntegrationAuditLog
from integrations.registry import register
from integrations.services.inbound_eracun_sync import (
    EracunSyncError,
    import_available_inbound_eracun,
    refresh_inbound_inbox,
)
from integrations.services.inbound_rejection import (
    submit_eracun_rejection,
    taxpayer_oib_for_tenant,
)
from invoices.models import Invoice
from super_integration.models import SuperDocumentLink


class PondiEracunError(Exception):
    pass


@register(IntegrationType.ERACUN, IntegrationProvider.PONDI)
class PondiEracunConnector:
    def __init__(self, config):
        self.config = config

    @property
    def tenant(self):
        return self.config.tenant

    def send_outbound(self, invoice, document, ubl_xml: str, *, correlation_id=None):
        return send_outbound_via_gateway(
            self.config,
            invoice,
            document,
            ubl_xml,
            correlation_id=correlation_id,
        )

    def sync_inbound(self) -> int:
        try:
            refresh_inbound_inbox(self.tenant, idempotency_key=str(uuid.uuid4()))
        except EracunSyncError:
            pass
        result = import_available_inbound_eracun(self.tenant)
        return int(result.get('imported') or 0)

    def poll_outbound_statuses(self) -> int:
        oib = taxpayer_oib_for_tenant(self.tenant)
        if not oib:
            return 0
        client = GatewayV1Client(taxpayer_oib=oib, timeout=90)
        try:
            client.start_reconciliation(oib, idempotency_key=str(uuid.uuid4()))
        except GatewayV1Error:
            return 0
        return 0

    def report_payment(self, invoice):
        return None

    def approve_inbound(self, expense, user=None):
        return None

    def reject_inbound(self, expense, description: str = ''):
        return submit_eracun_rejection(
            expense,
            reason_code='OTHER',
            reason_text=description or '',
            idempotency_key=str(uuid.uuid4()),
        )


@transaction.atomic
def send_outbound_via_gateway(config, invoice: Invoice, document, ubl_xml: str, *, correlation_id=None):
    from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

    tenant = config.tenant
    lock_open_vat_period_for_source_mutation(tenant, invoice.issue_date)
    if not invoice.invoice_number:
        invoice.assign_invoice_number()
        invoice.save(update_fields=['invoice_number'])

    existing = SuperDocumentLink.all_objects.filter(
        tenant=tenant,
        direction=SuperDocumentLink.DIRECTION_OUTBOUND,
        content_type=ContentType.objects.get_for_model(Invoice),
        object_id=invoice.pk,
    ).first()
    if existing and existing.super_guid:
        raise PondiEracunError('Račun je već poslan putem Pondi.')

    oib = taxpayer_oib_for_tenant(tenant)
    if not oib:
        raise PondiEracunError('Tvrtka nema ispravan OIB u postavkama.')

    document_type = 'CREDIT_NOTE' if getattr(document, 'is_credit_note', False) else 'INVOICE'
    client = GatewayV1Client(taxpayer_oib=oib, timeout=90)
    try:
        body = client.send_outbound_document(
            taxpayer_oib=oib,
            document_type=document_type,
            ubl=ubl_xml,
        )
    except GatewayV1Error as exc:
        if correlation_id:
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_OUTBOUND_FAILED,
                status=IntegrationAuditLog.STATUS_FAILED,
                correlation_id=correlation_id,
                invoice=invoice,
                integration_type=IntegrationType.ERACUN,
                provider=IntegrationProvider.PONDI,
                detail={'messages': [str(exc)]},
            )
        raise PondiEracunError(str(exc)) from exc

    refs = body.get('provider_refs') if isinstance(body.get('provider_refs'), dict) else {}
    guid = str(refs.get('invoice_guid') or body.get('provider_invoice_guid') or '').strip()
    processing = body.get('processing') if isinstance(body.get('processing'), dict) else {}
    if not guid:
        reason = processing.get('reason') or body.get('exchange_status') or 'GATEWAY_SEND_FAILED'
        raise PondiEracunError(f'Pondi slanje nije vratilo id dokumenta ({reason}).')

    link = SuperDocumentLink.all_objects.create(
        tenant=tenant,
        direction=SuperDocumentLink.DIRECTION_OUTBOUND,
        super_guid=guid,
        super_unique_id=str(refs.get('unique_id') or guid),
        ubl_xml=ubl_xml,
        content_type=ContentType.objects.get_for_model(Invoice),
        object_id=invoice.pk,
    )
    invoice.status = 'sent'
    invoice.save(update_fields=['status', 'updated_at'])

    if correlation_id:
        log_audit_step(
            tenant=tenant,
            step=IntegrationAuditLog.STEP_OUTBOUND_SENT,
            status=IntegrationAuditLog.STATUS_SUCCESS,
            correlation_id=correlation_id,
            invoice=invoice,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.PONDI,
            super_link=link,
            detail={'invoice_guid': guid, 'gateway_document_id': str(body.get('document_id') or '')},
        )
    return link
