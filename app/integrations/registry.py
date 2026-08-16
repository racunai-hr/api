from __future__ import annotations

from django.apps import apps

from .constants import IntegrationProvider, IntegrationType
from .errors import UnknownIntegrationError
from .models import IntegrationConfig
from .provider_loaders import load_provider_config

_REGISTRY: dict[tuple[str, str], type] = {}


def register(integration_type: str, provider: str):
    """Decorator that registers a connector class for a type/provider pair."""

    def decorator(cls):
        _REGISTRY[(integration_type, provider)] = cls
        return cls

    return decorator


def discover_connectors() -> None:
    """Import {app}.connector from each installed app to trigger @register decorators."""
    for app_config in apps.get_app_configs():
        module_name = f'{app_config.name}.connector'
        try:
            __import__(module_name)
        except ImportError:
            continue


def get_connector_class(integration_type: str, provider: str) -> type:
    key = (integration_type, provider)
    connector_cls = _REGISTRY.get(key)
    if connector_cls is None:
        raise UnknownIntegrationError(
            f'Nema registriranog connectora za {integration_type}/{provider}'
        )
    return connector_cls


def create(integration_config: IntegrationConfig):
    """Load provider credentials and instantiate the registered connector."""
    provider_config = load_provider_config(integration_config)
    connector_cls = get_connector_class(
        integration_config.integration_type,
        integration_config.provider,
    )
    return connector_cls(provider_config)


def registered_pairs() -> list[tuple[str, str]]:
    return list(_REGISTRY.keys())
