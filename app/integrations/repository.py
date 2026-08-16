from __future__ import annotations

import warnings

from django.conf import settings

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
        """Prefer DIRECT when DirectTenantConfig is active; SUPER only as rollback fallback."""
        direct_config = IntegrationConfig.all_objects.filter(
            tenant=tenant,
            integration_type=IntegrationType.ERACUN,
            provider=IntegrationProvider.DIRECT,
            environment=environment,
            is_active=True,
        ).first()
        if direct_config is not None:
            from fiscal_gateway.models import DirectTenantConfig

            if DirectTenantConfig.all_objects.filter(tenant=tenant, is_active=True).exists():
                return direct_config

        if getattr(settings, 'USE_SUPER_ERACUN_FALLBACK', True):
            super_config = IntegrationConfig.all_objects.filter(
                tenant=tenant,
                integration_type=IntegrationType.ERACUN,
                provider=IntegrationProvider.SUPER,
                environment=environment,
                is_active=True,
            ).first()
            if super_config is not None:
                from super_integration.models import SuperTenantConfig

                if SuperTenantConfig.all_objects.filter(tenant=tenant, is_active=True).exists():
                    warnings.warn(
                        'SUPER eRačun fallback is deprecated; configure DIRECT integration.',
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    return super_config

        return None

    @staticmethod
    def list_for_tenant(tenant):
        return IntegrationConfig.all_objects.filter(tenant=tenant).order_by(
            'integration_type',
            'provider',
            'environment',
        )
