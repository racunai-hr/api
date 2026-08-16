from __future__ import annotations

from django.conf import settings

from integrations.audit import log_audit_step, new_correlation_id
from integrations.constants import IntegrationEnvironment, IntegrationProvider, IntegrationType
from integrations.errors import MissingIntegrationError
from integrations.models import IntegrationAuditLog, IntegrationOutboxMessage
from integrations.repository import IntegrationRepository
from integrations.retry_policy import is_retryable_exception
from integrations.services.outbox import enqueue_outbox
from ubl.services.document_service import EracunDocumentService


class IntegrationManager:
    @staticmethod
    def _resolve_config(
        tenant,
        integration_type: str,
        environment: str = IntegrationEnvironment.PRODUCTION,
    ):
        if integration_type == IntegrationType.ERACUN:
            return IntegrationRepository.resolve_eracun_config(tenant, environment)
        return IntegrationRepository.get_active(tenant, integration_type, environment)

    @staticmethod
    def get_connector(
        tenant,
        integration_type: str,
        environment: str = IntegrationEnvironment.PRODUCTION,
    ):
        from integrations.registry import create

        config = IntegrationManager._resolve_config(tenant, integration_type, environment)
        if config is None:
            return None
        return create(config)

    @staticmethod
    def send_eracun(
        invoice,
        *,
        triggered_by=None,
        skip_outbox_enqueue: bool = False,
        environment: str = IntegrationEnvironment.PRODUCTION,
    ):
        eracun_config = IntegrationRepository.resolve_eracun_config(
            invoice.tenant,
            environment,
        )
        if eracun_config is None:
            raise MissingIntegrationError(
                f'Nema aktivne eRačun integracije za tenant {invoice.tenant.slug} '
                f'(okruženje: {environment}).'
            )

        correlation_id = new_correlation_id()
        sign = eracun_config.provider == IntegrationProvider.DIRECT
        document, ubl_xml = EracunDocumentService.build_from_invoice(
            invoice,
            correlation_id=correlation_id,
            sign=sign,
            triggered_by=triggered_by,
        )

        eracun = IntegrationManager.get_connector(
            invoice.tenant,
            IntegrationType.ERACUN,
            environment=environment,
        )

        try:
            link = eracun.send_outbound(
                invoice,
                document,
                ubl_xml,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            log_audit_step(
                tenant=invoice.tenant,
                step=IntegrationAuditLog.STEP_OUTBOUND_FAILED,
                status=IntegrationAuditLog.STATUS_FAILED,
                correlation_id=correlation_id,
                invoice=invoice,
                integration_type=eracun_config.integration_type,
                provider=eracun_config.provider,
                triggered_by=triggered_by,
                detail={'messages': [str(exc)]},
            )
            if not skip_outbox_enqueue and is_retryable_exception(exc):
                outbox = enqueue_outbox(
                    tenant=invoice.tenant,
                    operation=IntegrationOutboxMessage.OPERATION_SEND_ERACUN,
                    provider=eracun_config.provider,
                    correlation_id=correlation_id,
                    invoice=invoice,
                    payload_ref={'invoice_id': invoice.pk},
                    last_error=str(exc),
                )
                from fiscal_gateway.tasks import process_outbox_message_task

                process_outbox_message_task.apply_async(
                    args=[outbox.pk],
                    countdown=60,
                )
            raise

        fiscal = IntegrationManager._get_fiscal_connector(
            invoice.tenant,
            environment=environment,
        )
        if fiscal:
            fiscal_provider = IntegrationManager._resolve_fiscal_provider(invoice.tenant, environment)
            try:
                fiscal_log = fiscal.fiscalize(
                    invoice,
                    document,
                    ubl_xml,
                    correlation_id=correlation_id,
                )
                log_audit_step(
                    tenant=invoice.tenant,
                    step=IntegrationAuditLog.STEP_FISCALIZED,
                    status=IntegrationAuditLog.STATUS_SUCCESS,
                    correlation_id=correlation_id,
                    invoice=invoice,
                    integration_type=IntegrationType.FISCALIZATION,
                    provider=fiscal_provider,
                    triggered_by=triggered_by,
                    fiscal_log=fiscal_log,
                    detail={
                        'dry_run': getattr(fiscal_log, 'error_message', '') in {
                            'dry-run (bez slanja na CIS)',
                            'dry-run (bez slanja na Fiskal platformu)',
                        }
                    },
                )
            except Exception as exc:
                log_audit_step(
                    tenant=invoice.tenant,
                    step=IntegrationAuditLog.STEP_FISCAL_FAILED,
                    status=IntegrationAuditLog.STATUS_FAILED,
                    correlation_id=correlation_id,
                    invoice=invoice,
                    integration_type=IntegrationType.FISCALIZATION,
                    provider=fiscal_provider,
                    triggered_by=triggered_by,
                    detail={'messages': [str(exc)]},
                )
                raise

        return link

    @staticmethod
    def _resolve_fiscal_provider(tenant, environment: str = IntegrationEnvironment.PRODUCTION):
        if getattr(settings, 'USE_FISKAL_PLATFORM', False):
            return IntegrationProvider.FISKAL_PLATFORM
        config = IntegrationRepository.get_active(
            tenant,
            IntegrationType.FISCALIZATION,
            environment,
        )
        if config is None:
            return IntegrationProvider.CIS
        return config.provider

    @staticmethod
    def _get_fiscal_connector(tenant, *, environment: str = IntegrationEnvironment.PRODUCTION):
        if getattr(settings, 'USE_FISKAL_PLATFORM', False):
            from fiscal_gateway.connector_platform import FiskalPlatformFiscalizationConnector
            from fiscal_gateway.client.platform_client import FiscalPlatformConfig

            return FiskalPlatformFiscalizationConnector(FiscalPlatformConfig.from_tenant(tenant))

        return IntegrationManager.get_connector(
            tenant,
            IntegrationType.FISCALIZATION,
            environment=environment,
        )

    @staticmethod
    def sync_eracun_inbound(tenant) -> int:
        connector = IntegrationManager.get_connector(tenant, IntegrationType.ERACUN)
        if connector is None:
            return 0
        return connector.sync_inbound()

    @staticmethod
    def poll_eracun_outbound(tenant) -> int:
        connector = IntegrationManager.get_connector(tenant, IntegrationType.ERACUN)
        if connector is None:
            return 0
        return connector.poll_outbound_statuses()

    @staticmethod
    def approve_inbound_expense(expense, user=None):
        connector = IntegrationManager.get_connector(expense.tenant, IntegrationType.ERACUN)
        if connector is None:
            raise MissingIntegrationError(
                f'Nema aktivne eRačun integracije za tenant {expense.tenant.slug}.'
            )
        return connector.approve_inbound(expense, user=user)

    @staticmethod
    def reject_inbound_expense(expense, description: str = ''):
        connector = IntegrationManager.get_connector(expense.tenant, IntegrationType.ERACUN)
        if connector is None:
            raise MissingIntegrationError(
                f'Nema aktivne eRačun integracije za tenant {expense.tenant.slug}.'
            )
        return connector.reject_inbound(expense, description=description)

    @staticmethod
    def report_invoice_payment(invoice):
        connector = IntegrationManager.get_connector(invoice.tenant, IntegrationType.ERACUN)
        if connector is None:
            return None
        return connector.report_payment(invoice)
