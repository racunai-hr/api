from __future__ import annotations

from .constants import IntegrationEnvironment, IntegrationProvider, IntegrationType
from .models import IntegrationConfig


class IntegrationRepository:
    @staticmethod
    def get_active(
        tenant,
        integration_type: str,
        environment: str = IntegrationEnvironment.PRODUCTION,
    ) -> IntegrationConfig | None:
        return IntegrationConfig.all_objects.filter(
            tenant=tenant,
            integration_type=integration_type,
            environment=environment,
            is_active=True,
        ).first()

    @staticmethod
    def resolve_eracun_config(
        tenant,
        environment: str = IntegrationEnvironment.PRODUCTION,
    ) -> IntegrationConfig | None:
        """Resolve eRačun integration — DIRECT only (M1.7: no SUPER routing)."""
        direct_config = IntegrationConfig.all_objects.filter(
            tenant=tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            environment=environment,
            is_active=True,
        ).first()
        if direct_config is None:
            return None

        from fiscal_gateway.models import DirectTenantConfig

        if DirectTenantConfig.all_objects.filter(tenant=tenant, is_active=True).exists():
            return direct_config

        return None

    @staticmethod
    def list_for_tenant(tenant):
        return IntegrationConfig.all_objects.filter(tenant=tenant).order_by(
            'integration_type',
            'provider',
            'environment',
        )
