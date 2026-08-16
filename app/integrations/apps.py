from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'integrations'
    verbose_name = 'Integracije'

    def ready(self):
        from integrations.registry import discover_connectors

        discover_connectors()
