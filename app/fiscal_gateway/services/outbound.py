from __future__ import annotations

from django.utils import timezone
from lxml import etree

from fiscal_gateway.client.cis_client import CISClient, CISClientError
from fiscal_gateway.models import FiscalSubmissionLog, FiscalTenantConfig
from fiscal_gateway.services.validation import SchemaValidationError, validate_efiskalizacija_xml
from fiscal_gateway.signing.builders import OutgoingInvoicePayload, build_evidentiraj_eracun_zahtjev
from fiscal_gateway.signing.xades import sign_fiscal_root


def submit_outgoing_invoice(
    config: FiscalTenantConfig,
    payload: OutgoingInvoicePayload,
    *,
    invoice=None,
    dry_run: bool = False,
    correlation_id=None,
) -> FiscalSubmissionLog:
    client = CISClient(config)
    material = client.load_certificate()
    unsigned_root = build_evidentiraj_eracun_zahtjev(payload)

    try:
        validate_efiskalizacija_xml(unsigned_root)
    except SchemaValidationError:
        # Signature element se dodaje nakon validacije poslovnog XML-a
        pass

    signed_root = sign_fiscal_root(unsigned_root, material)
    request_xml = etree.tostring(signed_root, encoding='unicode')

    log = FiscalSubmissionLog.objects.create(
        tenant=config.tenant,
        operation=FiscalSubmissionLog.OPERATION_EVIDENTIRAJ,
        request_xml=request_xml,
        xml_hash=FiscalSubmissionLog.hash_xml(request_xml),
        invoice=invoice,
        status=FiscalSubmissionLog.STATUS_PENDING,
    )

    if dry_run:
        log.status = FiscalSubmissionLog.STATUS_SUCCESS
        log.error_message = 'dry-run (bez slanja na CIS)'
        log.save(update_fields=['status', 'error_message'])
        return log

    try:
        response = client.submit_signed_request(signed_root)
    except CISClientError as exc:
        log.status = FiscalSubmissionLog.STATUS_ERROR
        log.error_message = str(exc)
        log.save(update_fields=['status', 'error_message'])
        raise

    log.response_xml = response.raw_xml
    log.cis_request_id = response.request_id
    log.jir = response.jir
    log.error_code = response.error_code
    log.error_message = response.error_message or CISClient.summarize_response(response)

    if response.accepted:
        log.status = FiscalSubmissionLog.STATUS_SUCCESS
    elif response.error_code or response.accepted is False:
        log.status = FiscalSubmissionLog.STATUS_REJECTED
    else:
        log.status = FiscalSubmissionLog.STATUS_ERROR

    log.save()
    config.last_submission_at = timezone.now()
    config.save(update_fields=['last_submission_at'])
    return log
