"""Tenant-scoped access for partners MDM API (ADR-0022 §6)."""

from rest_framework.permissions import BasePermission

from tenants.permissions import get_role, role_can_view, role_can_write


class TenantPartnersReadPermission(BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return False
        tenant = getattr(request, 'tenant', None)
        if tenant is None:
            return False
        return role_can_view(get_role(user, tenant))


class TenantPartnersWritePermission(BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return False
        tenant = getattr(request, 'tenant', None)
        if tenant is None:
            return False
        return role_can_write(get_role(user, tenant))
