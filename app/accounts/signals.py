"""Audit log signali za ključne poslovne modele."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from tenants.context import get_current_tenant
from tenants.user_context import get_current_user

AUDITED_MODELS: tuple[str, ...] = (
    'invoices.Invoice',
    'payments.Payment',
    'expenses.Expense',
    'accounting.JournalEntry',
    'partners.Partner',
    'banking.BankStatement',
    'settings.CompanySettings',
    'tenants.TenantMembership',
)

_pre_save_state: dict[int, dict] = {}


def _model_label(instance) -> str:
    return f'{instance._meta.app_label}.{instance._meta.model_name}'


def _get_tenant(instance):
    tenant = get_current_tenant()
    if tenant is not None:
        return tenant
    if hasattr(instance, 'tenant_id') and instance.tenant_id:
        return instance.tenant
    return None


def _serialize(instance) -> dict:
    data = {}
    for field in instance._meta.fields:
        if field.name in ('id',):
            continue
        value = getattr(instance, field.name, None)
        if hasattr(value, 'pk'):
            value = value.pk
        data[field.name] = str(value) if value is not None else None
    return data


def _write_audit(instance, action: str, changes: dict | None = None):
    from accounts.models import AuditLog

    tenant = _get_tenant(instance)
    if tenant is None:
        return

    AuditLog.all_objects.create(
        tenant=tenant,
        user=get_current_user(),
        action=action,
        model_name=_model_label(instance),
        object_id=str(instance.pk),
        changes=changes or {},
    )


def _connect_model(model_path: str):
    from django.apps import apps

    app_label, model_name = model_path.split('.')
    model = apps.get_model(app_label, model_name)

    @receiver(pre_save, sender=model)
    def capture_old_state(sender, instance, **kwargs):
        if instance.pk:
            try:
                old = sender.all_objects.get(pk=instance.pk) if hasattr(sender, 'all_objects') else sender.objects.get(pk=instance.pk)
                _pre_save_state[instance.pk] = _serialize(old)
            except sender.DoesNotExist:
                pass

    @receiver(post_save, sender=model)
    def audit_save(sender, instance, created, **kwargs):
        if created:
            _write_audit(instance, 'create', {'new': _serialize(instance)})
        else:
            old = _pre_save_state.pop(instance.pk, {})
            new = _serialize(instance)
            diff = {k: {'old': old.get(k), 'new': new.get(k)} for k in new if old.get(k) != new.get(k)}
            if diff:
                _write_audit(instance, 'update', diff)

    @receiver(post_delete, sender=model)
    def audit_delete(sender, instance, **kwargs):
        _pre_save_state.pop(instance.pk, None)
        _write_audit(instance, 'delete', {'deleted': _serialize(instance)})


def connect_audit_signals():
    for model_path in AUDITED_MODELS:
        _connect_model(model_path)
