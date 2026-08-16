from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from lxml import etree

from fiscal_gateway.client.as4_client import As4SendError, submit_application_response_via_domibus
from fiscal_gateway.client.domibus_push import DomibusPushMessage, oib_from_participant
from fiscal_gateway.client.eracun_lookup import INVOICE_NS, UBL_NS
from fiscal_gateway.services.application_response import (
    build_application_response_xml,
    wrap_application_response_sbdh,
)

from fiscal_gateway.services.inbound_expense import import_inbound_as4_expense, resolve_tenant_for_customer_oib
from integrations.audit import log_audit_step, new_correlation_id
from integrations.constants import IntegrationProvider, IntegrationType
from integrations.models import IntegrationAuditLog

logger = logging.getLogger(__name__)


class InboundAs4Error(Exception):
    pass


@dataclass(frozen=True)
class InboundValidationResult:
    accepted: bool
    errors: tuple[str, ...]
    invoice_id: str
    supplier_oib: str
    customer_oib: str


@dataclass(frozen=True)
class InboundAs4Result:
    accepted: bool
    invoice_id: str
    response_message_id: str
    validation: InboundValidationResult
    skipped: bool = False
    detail: str = ''


def _find_invoice(payload_xml: bytes) -> etree._Element:
    root = etree.fromstring(payload_xml)
    invoice = root.find(f'.//{{{INVOICE_NS}}}Invoice')
    if invoice is None and root.tag == f'{{{INVOICE_NS}}}Invoice':
        invoice = root
    if invoice is None:
        raise InboundAs4Error('Payload ne sadrži UBL Invoice')
    return invoice


def validate_inbound_invoice(invoice_root: etree._Element) -> InboundValidationResult:
    errors: list[str] = []
    invoice_id = (invoice_root.findtext('cbc:ID', namespaces=UBL_NS) or '').strip()
    supplier_oib = (
        invoice_root.findtext(
            './/cac:AccountingSupplierParty/cac:Party/cbc:EndpointID',
            namespaces=UBL_NS,
        )
        or ''
    ).strip()
    customer_oib = (
        invoice_root.findtext(
            './/cac:AccountingCustomerParty/cac:Party/cbc:EndpointID',
            namespaces=UBL_NS,
        )
        or ''
    ).strip()

    if not invoice_id:
        errors.append('Nedostaje cbc:ID')
    if not invoice_root.findtext('cbc:IssueDate', namespaces=UBL_NS):
        errors.append('Nedostaje cbc:IssueDate')
    if not invoice_root.findtext('cbc:CustomizationID', namespaces=UBL_NS):
        errors.append('Nedostaje cbc:CustomizationID')
    if not supplier_oib:
        errors.append('Nedostaje AccountingSupplierParty/cbc:EndpointID')
    if not customer_oib:
        errors.append('Nedostaje AccountingCustomerParty/cbc:EndpointID')
    if not invoice_root.findtext('.//cac:LegalMonetaryTotal/cbc:PayableAmount', namespaces=UBL_NS):
        errors.append('Nedostaje LegalMonetaryTotal/cbc:PayableAmount')

    expected_oib = getattr(settings, 'DOMIBUS_AP_OIB', '').strip()
    if expected_oib and customer_oib and customer_oib != expected_oib:
        errors.append(f'Primatelj {customer_oib} ne odgovara AP OIB-u {expected_oib}')

    return InboundValidationResult(
        accepted=not errors,
        errors=tuple(errors),
        invoice_id=invoice_id,
        supplier_oib=supplier_oib,
        customer_oib=customer_oib,
    )


def handle_inbound_push(message: DomibusPushMessage) -> InboundAs4Result:
    if message.operation == 'receiveFailure':
        logger.warning(
            'Domibus receiveFailure message_id=%s code=%s detail=%s',
            message.message_id,
            message.error_code,
            message.error_detail,
        )
        return InboundAs4Result(
            accepted=False,
            invoice_id='',
            response_message_id='',
            validation=InboundValidationResult(False, (message.error_detail or message.error_code,), '', '', ''),
            skipped=True,
            detail='receiveFailure',
        )

    if message.operation != 'submitMessage' or not message.payload_xml:
        raise InboundAs4Error(f'Nepodržana push operacija: {message.operation}')

    invoice_root = _find_invoice(message.payload_xml)
    validation = validate_inbound_invoice(invoice_root)
    receiver_oib = validation.customer_oib or oib_from_participant(message.final_recipient)
    tenant = resolve_tenant_for_customer_oib(validation.customer_oib)
    correlation_id = new_correlation_id()

    if tenant:
        log_audit_step(
            tenant=tenant,
            step=IntegrationAuditLog.STEP_INBOUND_RECEIVED,
            status=IntegrationAuditLog.STATUS_SUCCESS,
            correlation_id=correlation_id,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            detail={
                'message_id': message.message_id,
                'invoice_id': validation.invoice_id,
                'accepted': validation.accepted,
            },
        )
    reason = '; '.join(validation.errors)

    app_response = build_application_response_xml(
        invoice_root=invoice_root,
        receiver_oib=receiver_oib,
        accepted=validation.accepted,
        reason=reason,
    )
    payload = wrap_application_response_sbdh(
        app_response.xml,
        sender_oib=receiver_oib,
        receiver_oib=validation.supplier_oib,
        response_id=app_response.response_id,
    )

    to_party_id = message.from_party_id.strip()
    if not to_party_id:
        raise InboundAs4Error('Nedostaje From party u ebMS zaglavlju')

    send_result = submit_application_response_via_domibus(
        payload,
        to_ap_party_id=to_party_id,
        ref_to_message_id=message.message_id,
        conversation_id=message.conversation_id,
        supplier_oib=validation.supplier_oib,
        receiver_oib=receiver_oib,
    )

    expense = None
    if validation.accepted:
        if tenant and message.payload_xml:
            invoice_xml = etree.tostring(invoice_root, encoding='unicode')
            try:
                expense = import_inbound_as4_expense(
                    tenant=tenant,
                    ubl_xml=invoice_xml,
                    message_id=message.message_id,
                    supplier_oib=validation.supplier_oib,
                    customer_oib=validation.customer_oib,
                    from_party_id=message.from_party_id,
                    conversation_id=message.conversation_id,
                )
                log_audit_step(
                    tenant=tenant,
                    step=IntegrationAuditLog.STEP_EXPENSE_CREATED,
                    status=IntegrationAuditLog.STATUS_SUCCESS,
                    correlation_id=correlation_id,
                    integration_type=IntegrationType.ERACUN,
                    provider=IntegrationProvider.DIRECT,
                    detail={
                        'message_id': message.message_id,
                        'expense_id': expense.pk,
                        'invoice_id': validation.invoice_id,
                    },
                )
            except Exception:
                logger.exception(
                    'AS4 inbound expense import failed message_id=%s',
                    message.message_id,
                )
                log_audit_step(
                    tenant=tenant,
                    step=IntegrationAuditLog.STEP_EXPENSE_CREATED,
                    status=IntegrationAuditLog.STATUS_FAILED,
                    correlation_id=correlation_id,
                    integration_type=IntegrationType.ERACUN,
                    provider=IntegrationProvider.DIRECT,
                    detail={
                        'message_id': message.message_id,
                        'invoice_id': validation.invoice_id,
                    },
                )

    if tenant:
        log_audit_step(
            tenant=tenant,
            step=IntegrationAuditLog.STEP_APP_RESPONSE_SENT,
            status=IntegrationAuditLog.STATUS_SUCCESS,
            correlation_id=correlation_id,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            detail={
                'inbound_message_id': message.message_id,
                'response_message_id': send_result.message_id,
                'accepted': validation.accepted,
            },
        )

    logger.info(
        'AS4 inbound invoice=%s accepted=%s response_message_id=%s expense=%s',
        validation.invoice_id,
        validation.accepted,
        send_result.message_id,
        expense.pk if expense else None,
    )
    return InboundAs4Result(
        accepted=validation.accepted,
        invoice_id=validation.invoice_id,
        response_message_id=send_result.message_id,
        validation=validation,
    )
