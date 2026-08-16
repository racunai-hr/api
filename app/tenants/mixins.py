from django.db import models

from .context import get_current_tenant
from .permissions import (
    get_role,
    role_can_delete,
    role_can_view,
    role_can_write_model,
)


class TenantQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)


class TenantManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant is not None:
            return qs.filter(tenant=tenant)
        return qs


class TenantMixin(models.Model):
    """Abstract mixin — dodaje tenant FK poslovnim modelima."""

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='%(app_label)s_%(class)s_set',
        verbose_name='Tenant',
    )

    objects = TenantManager.from_queryset(TenantQuerySet)()
    all_objects = models.Manager()

    class Meta:
        abstract = True


class TenantAdminMixin:
    """Admin mixin — filtrira queryset i postavlja tenant pri spremanju."""

    tenant_lookup = None

    def _filter_queryset_for_tenant(self, qs, tenant):
        if hasattr(qs.model, 'tenant_id'):
            if tenant is None:
                return qs
            return qs.filter(tenant=tenant)

        lookup = getattr(self, 'tenant_lookup', None)
        if lookup:
            if tenant is None:
                return qs
            return qs.filter(**{lookup: tenant})

        return qs.none()

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        tenant = getattr(request, 'tenant', None)

        if tenant is not None:
            return self._filter_queryset_for_tenant(qs, tenant)

        if getattr(request, 'is_platform_admin', False):
            user = getattr(request, 'user', None)
            if user is not None and user.is_authenticated and user.is_superuser:
                return qs

        return qs.none()

    def save_model(self, request, obj, form, change):
        if (
            not change
            and hasattr(obj, 'tenant_id')
            and not obj.tenant_id
            and getattr(request, 'tenant', None)
        ):
            obj.tenant = request.tenant
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if (
                hasattr(instance, 'tenant_id')
                and not instance.tenant_id
                and getattr(request, 'tenant', None)
            ):
                instance.tenant = request.tenant
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()

    def _user_role(self, request):
        tenant = getattr(request, 'tenant', None)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and user.is_superuser:
            return 'owner'
        return get_role(user, tenant)

    def has_module_permission(self, request):
        if not super().has_module_permission(request):
            return False
        role = self._user_role(request)
        if role is None and getattr(request, 'is_platform_admin', False):
            return request.user.is_superuser
        return role_can_view(role)

    def has_view_permission(self, request, obj=None):
        if not super().has_view_permission(request, obj):
            return False
        role = self._user_role(request)
        if role is None and request.user.is_superuser:
            return True
        return role_can_view(role)

    def has_add_permission(self, request):
        if not super().has_add_permission(request):
            return False
        role = self._user_role(request)
        if role is None and request.user.is_superuser:
            return True
        app_label = self.model._meta.app_label
        return role_can_write_model(role, app_label)

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        role = self._user_role(request)
        if role is None and request.user.is_superuser:
            return True
        app_label = self.model._meta.app_label
        return role_can_write_model(role, app_label)

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        role = self._user_role(request)
        if role is None and request.user.is_superuser:
            return True
        app_label = self.model._meta.app_label
        return role_can_delete(role, app_label)
