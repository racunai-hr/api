"""Tenant-scoped read access for the document API (ADR-0020 §12)."""

from rest_framework.permissions import BasePermission

from tenants.permissions import get_role, role_can_view


class TenantDocumentReadPermission(BasePermission):
    """Authenticated users with a view role on request.tenant. Missing tenant is denied here; views map that to 404."""

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return False
        tenant = getattr(request, 'tenant', None)
        if tenant is None:
            return False
        return role_can_view(get_role(user, tenant))
