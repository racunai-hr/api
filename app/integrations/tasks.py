from celery import shared_task

from integrations.constants import IntegrationType
from integrations.manager import IntegrationManager
from integrations.models import IntegrationConfig


@shared_task(name='integrations.sync_eracun_inbound')
def sync_eracun_inbound_task(integration_config_id: int) -> int:
    config = IntegrationConfig.all_objects.get(
        pk=integration_config_id,
        integration_type=IntegrationType.ERACUN,
        is_active=True,
    )
    return IntegrationManager.sync_eracun_inbound(config.tenant)


@shared_task(name='integrations.poll_eracun_outbound')
def poll_eracun_outbound_task(integration_config_id: int) -> int:
    config = IntegrationConfig.all_objects.get(
        pk=integration_config_id,
        integration_type=IntegrationType.ERACUN,
        is_active=True,
    )
    return IntegrationManager.poll_eracun_outbound(config.tenant)


@shared_task(name='integrations.sync_all_eracun_tenants')
def sync_all_eracun_tenants_task():
    for config in IntegrationConfig.all_objects.filter(
        integration_type=IntegrationType.ERACUN,
        is_active=True,
    ):
        IntegrationManager.sync_eracun_inbound(config.tenant)
        IntegrationManager.poll_eracun_outbound(config.tenant)

    from fiscal_gateway.tasks import process_due_outbox_messages_task

    process_due_outbox_messages_task.delay()
