"""RBAC pomoćnici za tenant uloge (owner / accountant / viewer)."""

from __future__ import annotations

from django.contrib.auth.models import User

from tenants.models import Tenant, TenantMembership

ACCOUNTING_APP_LABELS = frozenset({'accounting', 'banking'})

ROLE_LEVEL = {
    'viewer': 1,
    'accountant': 2,
    'owner': 3,
}


def get_membership(user: User, tenant: Tenant | None) -> TenantMembership | None:
    if user is None or not user.is_authenticated or tenant is None:
        return None
    if user.is_superuser:
        return None
    return (
        TenantMembership.objects.filter(user=user, tenant=tenant)
        .select_related('tenant')
        .first()
    )


def get_role(user: User, tenant: Tenant | None) -> str | None:
    if user is not None and user.is_authenticated and user.is_superuser:
        return 'owner'
    membership = get_membership(user, tenant)
    return membership.role if membership else None


def role_can_view(role: str | None) -> bool:
    return role in ROLE_LEVEL


def role_can_export(role: str | None) -> bool:
    return role in ROLE_LEVEL


def role_can_write(role: str | None) -> bool:
    return role in ('owner', 'accountant')


def role_can_manage_tenant(role: str | None) -> bool:
    return role == 'owner'


def role_can_write_model(role: str | None, app_label: str) -> bool:
    if role == 'owner':
        return True
    if role == 'accountant':
        return app_label in ACCOUNTING_APP_LABELS or app_label in {
            'invoices', 'payments', 'expenses', 'partners', 'settings', 'dashboard',
        }
    return False


def role_can_delete(role: str | None, app_label: str) -> bool:
    if role == 'viewer':
        return False
    return role_can_write_model(role, app_label)
