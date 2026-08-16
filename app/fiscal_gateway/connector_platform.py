from __future__ import annotations

from integrations.constants import IntegrationProvider, IntegrationType
from integrations.registry import register
from fiscal_gateway.adapters.ubl_to_platform import ubl_document_to_platform_payload
from fiscal_gateway.client.platform_client import FiscalPlatformClient, FiscalPlatformConfig
from fiscal_gateway.models import FiscalSubmissionLog


@register(IntegrationType.FISCALIZATION, IntegrationProvider.FISKAL_PLATFORM)
class FiskalPlatformFiscalizationConnector:
    def __init__(self, config: FiscalPlatformConfig):
        self.config = config
        self._client = FiscalPlatformClient(config)

    @property
    def tenant(self):
        return self.config.tenant

    def fiscalize(
        self,
        invoice,
        document,
        ubl_xml: str,
        *,
        dry_run: bool = False,
        correlation_id=None,
    ) -> FiscalSubmissionLog:
        payload = ubl_document_to_platform_payload(document)
        external_reference = getattr(document, 'document_number', None) or payload.get(
            'document_number'
        )
        idempotency_key = None
        if invoice is not None and getattr(invoice, 'pk', None):
            idempotency_key = f'racunai-invoice-{invoice.pk}'

        log = FiscalSubmissionLog.objects.create(
            tenant=self.config.tenant,
            operation=FiscalSubmissionLog.OPERATION_EVIDENTIRAJ,
            status=FiscalSubmissionLog.STATUS_PENDING,
            invoice=invoice,
        )

        try:
            result = self._client.submit_and_wait(
                payload=payload,
                correlation_id=correlation_id,
                external_reference=external_reference,
                idempotency_key=idempotency_key,
                dry_run=dry_run,
            )
        except Exception as exc:
            log.status = FiscalSubmissionLog.STATUS_ERROR
            log.error_message = str(exc)
            log.save(update_fields=['status', 'error_message'])
            raise

        if dry_run or result.get('dry_run'):
            log.status = FiscalSubmissionLog.STATUS_SUCCESS
            log.jir = result.get('jir', '')
            log.error_message = 'dry-run (bez slanja na Fiskal platformu)'
            log.save(update_fields=['status', 'jir', 'error_message'])
            return log

        status = (result.get('status') or '').lower()
        log.jir = result.get('jir') or ''
        log.cis_request_id = result.get('cis_request_id') or ''
        log.error_code = result.get('error_code') or ''
        log.error_message = result.get('error_message') or ''

        if status == 'accepted':
            log.status = FiscalSubmissionLog.STATUS_SUCCESS
        elif result.get('error_code'):
            log.status = FiscalSubmissionLog.STATUS_REJECTED
        else:
            log.status = FiscalSubmissionLog.STATUS_ERROR

        log.save()
        if log.status != FiscalSubmissionLog.STATUS_SUCCESS:
            raise RuntimeError(log.error_message or f'Fiskal platform status: {status}')
        return log
