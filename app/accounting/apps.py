from django.apps import AppConfig


class AccountingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounting'

    def ready(self):
        from . import signals  # noqa: F401

        from events.dispatcher import register_handler
        from events.payment import PaymentExecuted

        from accounting.services.payment_posting import handle_payment_executed

        register_handler(PaymentExecuted, handle_payment_executed)
