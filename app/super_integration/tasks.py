"""Backward-compatible Celery task aliases — use integrations.tasks instead."""

import warnings

from celery import shared_task

from integrations.constants import IntegrationType
from integrations.manager import IntegrationManager
from integrations.models import IntegrationConfig
from integrations.tasks import (
    poll_eracun_outbound_task,
    sync_all_eracun_tenants_task,
    sync_eracun_inbound_task,
)


def _deprecation_notice():
    warnings.warn(
        'super_integration Celery tasks are deprecated; use integrations.tasks.',
        DeprecationWarning,
        stacklevel=3,
    )


@shared_task(name='super_integration.sync_inbound_invoices')
def sync_inbound_invoices_task(integration_config_id: int) -> int:
    _deprecation_notice()
    return sync_eracun_inbound_task(integration_config_id)


@shared_task(name='super_integration.poll_outbound_statuses')
def poll_outbound_statuses_task(integration_config_id: int) -> int:
    _deprecation_notice()
    return poll_eracun_outbound_task(integration_config_id)


@shared_task(name='super_integration.sync_all_tenants')
def sync_all_tenants_task():
    _deprecation_notice()
    return sync_all_eracun_tenants_task()
