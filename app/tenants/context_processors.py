from django.conf import settings

from tenants.branding import admin_branding_context
from tenants.models import Tenant


def admin_branding(request):
    return admin_branding_context(request)


def tenant_switcher(request):
    """Kontekst za superuser tenant switcher u adminu."""
    if not getattr(request, 'is_platform_admin', False):
        return {}
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or not user.is_superuser:
        return {}

    session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_tenant_id')
    active_id = request.session.get(session_key)
    tenants = list(Tenant.objects.filter(is_active=True).order_by('name'))
    active_tenant = next((t for t in tenants if t.pk == active_id), None)

    return {
        'platform_tenants': tenants,
        'platform_active_tenant': active_tenant,
        'platform_tenant_session_key': session_key,
    }
