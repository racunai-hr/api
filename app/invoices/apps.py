# /srv/finestar/erp/app/invoices/apps.py
from django.apps import AppConfig

class InvoicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "invoices"

    def ready(self):
        # registriraj signale
        from . import signals  # noqa: F401
