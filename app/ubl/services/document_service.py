from __future__ import annotations

import time
import uuid

from django.conf import settings

from settings.models import CompanySettings
from ubl.builder.invoice import render_invoice_ubl
from ubl.domain.document import UblDocument
from ubl.domain.errors import DocumentBuildError, UblError
from ubl.serializers.from_invoice import invoice_to_ubl_document
from ubl.signing.xades import UblSigningError, sign_invoice_ubl
from ubl.validators.schematron import SchematronSkipped, validate_schematron
from ubl.validators.semantic import validate_semantic
from ubl.validators.xsd import validate_xsd

from integrations.audit import log_audit_step, measure_ms, timed_detail
from integrations.models import IntegrationAuditLog


class EracunDocumentService:
    @staticmethod
    def build_from_invoice(
        invoice,
        *,
        correlation_id: uuid.UUID | None = None,
        sign: bool = False,
        profile_version: str = '2025',
        triggered_by=None,
    ) -> tuple[UblDocument, str]:
        correlation_id = correlation_id or uuid.uuid4()
        tenant = invoice.tenant
        started = time.perf_counter()

        company = CompanySettings.all_objects.filter(tenant=tenant).first()
        if not company:
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_DOCUMENT_BUILT,
                status=IntegrationAuditLog.STATUS_FAILED,
                correlation_id=correlation_id,
                invoice=invoice,
                triggered_by=triggered_by,
                detail={'messages': ['Postavke tvrtke nisu konfigurirane']},
            )
            raise DocumentBuildError(['Postavke tvrtke nisu konfigurirane'])

        partner = invoice.company_to
        document = invoice_to_ubl_document(invoice, company, partner)
        log_audit_step(
            tenant=tenant,
            step=IntegrationAuditLog.STEP_DOCUMENT_BUILT,
            status=IntegrationAuditLog.STATUS_SUCCESS,
            correlation_id=correlation_id,
            invoice=invoice,
            triggered_by=triggered_by,
            detail=timed_detail(duration_ms=measure_ms(started)),
        )

        try:
            started = time.perf_counter()
            validate_semantic(document)
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_SEMANTIC_OK,
                status=IntegrationAuditLog.STATUS_SUCCESS,
                correlation_id=correlation_id,
                invoice=invoice,
                triggered_by=triggered_by,
                detail=timed_detail(duration_ms=measure_ms(started)),
            )
        except UblError as exc:
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_SEMANTIC_OK,
                status=IntegrationAuditLog.STATUS_FAILED,
                correlation_id=correlation_id,
                invoice=invoice,
                triggered_by=triggered_by,
                detail={'messages': exc.messages},
            )
            raise

        xml = render_invoice_ubl(document, profile_version=profile_version)

        try:
            started = time.perf_counter()
            validate_xsd(xml)
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_XSD_OK,
                status=IntegrationAuditLog.STATUS_SUCCESS,
                correlation_id=correlation_id,
                invoice=invoice,
                triggered_by=triggered_by,
                detail=timed_detail(duration_ms=measure_ms(started)),
            )
        except UblError as exc:
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_XSD_OK,
                status=IntegrationAuditLog.STATUS_FAILED,
                correlation_id=correlation_id,
                invoice=invoice,
                triggered_by=triggered_by,
                detail={'messages': exc.messages},
            )
            raise

        try:
            started = time.perf_counter()
            validate_schematron(xml)
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_SCHEMATRON_OK,
                status=IntegrationAuditLog.STATUS_SUCCESS,
                correlation_id=correlation_id,
                invoice=invoice,
                triggered_by=triggered_by,
                detail=timed_detail(duration_ms=measure_ms(started)),
            )
        except SchematronSkipped as exc:
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_SCHEMATRON_SKIPPED,
                status=IntegrationAuditLog.STATUS_SKIPPED,
                correlation_id=correlation_id,
                invoice=invoice,
                triggered_by=triggered_by,
                detail={'reason': str(exc)},
            )
        except UblError as exc:
            log_audit_step(
                tenant=tenant,
                step=IntegrationAuditLog.STEP_SCHEMATRON_OK,
                status=IntegrationAuditLog.STATUS_FAILED,
                correlation_id=correlation_id,
                invoice=invoice,
                triggered_by=triggered_by,
                detail={'messages': exc.messages},
            )
            raise

        if sign:
            try:
                started = time.perf_counter()
                xml = sign_invoice_ubl(xml, tenant=tenant)
                log_audit_step(
                    tenant=tenant,
                    step=IntegrationAuditLog.STEP_SIGNED,
                    status=IntegrationAuditLog.STATUS_SUCCESS,
                    correlation_id=correlation_id,
                    invoice=invoice,
                    triggered_by=triggered_by,
                    detail=timed_detail(duration_ms=measure_ms(started)),
                )
            except UblSigningError as exc:
                log_audit_step(
                    tenant=tenant,
                    step=IntegrationAuditLog.STEP_SIGNED,
                    status=IntegrationAuditLog.STATUS_FAILED,
                    correlation_id=correlation_id,
                    invoice=invoice,
                    triggered_by=triggered_by,
                    detail={'messages': exc.messages},
                )
                raise

        return document, xml
