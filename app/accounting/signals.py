from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounting.services.posting import post_document, post_invoice_payment


def _get_system_user(tenant):
    User = get_user_model()
    user = User.objects.filter(is_superuser=True).first()
    return user


@receiver(post_save, sender='invoices.Invoice')
def auto_post_invoice(sender, instance, created, **kwargs):
    user = _get_system_user(instance.tenant)
    if not user:
        return
    if instance.status in ('sent', 'paid', 'overdue'):
        post_document(instance.tenant, instance, 'invoice_issued', user)
    if instance.status == 'paid':
        has_completed_payments = instance.payments.filter(status='completed').exists()
        if not has_completed_payments:
            post_document(instance.tenant, instance, 'invoice_paid', user)
        try:
            from integrations.services.invoice_integration import InvoiceIntegrationService

            InvoiceIntegrationService.report_payment(instance)
        except Exception:
            pass


@receiver(post_save, sender='expenses.Expense')
def auto_post_expense(sender, instance, created, **kwargs):
    user = _get_system_user(instance.tenant)
    if not user:
        return
    if instance.status in ('approved', 'paid'):
        post_document(instance.tenant, instance, 'expense_approved', user)
    if instance.status == 'paid':
        post_document(instance.tenant, instance, 'expense_paid', user)


@receiver(post_save, sender='payments.Payment')
def auto_post_payment(sender, instance, created, **kwargs):
    user = _get_system_user(instance.tenant)
    if not user or instance.status != 'completed':
        return
    if instance.related_invoice_id and instance.related_invoice.status == 'paid':
        post_invoice_payment(instance.tenant, instance.related_invoice, instance, user)
