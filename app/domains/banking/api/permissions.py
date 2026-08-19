"""Tenant-scoped write access for banking API (ADR-0021 §6)."""

from rest_framework.permissions import BasePermission

from tenants.permissions import get_role, role_can_view, role_can_write


class TenantBankingReadPermission(BasePermission):
    """Authenticated users with a view role on request.tenant."""

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return False
        tenant = getattr(request, 'tenant', None)
        if tenant is None:
            return False
        return role_can_view(get_role(user, tenant))


class TenantBankingWritePermission(BasePermission):
    """Owner/accountant write; viewer is denied (views map to 404)."""

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return False
        tenant = getattr(request, 'tenant', None)
        if tenant is None:
            return False
        return role_can_write(get_role(user, tenant))
