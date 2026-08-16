from django.core.cache import cache
from django.http import Http404

from . import context
from . import user_context
from .resolution import (
    get_cached_custom_domains,
    is_platform_admin_host,
    normalize_host,
    resolve_platform_tenant,
    resolve_tenant_from_host,
)
from .service_hosts import ServiceHostKind, is_as4_inbound_path, resolve_service_host


class DynamicAllowedHostsMiddleware:
    """Dopušta HTTP Host za aktivne Tenant.custom_domain vrijednosti."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = normalize_host(request.META.get('HTTP_HOST', ''))
        if host:
            self._ensure_host_allowed(host)
        return self.get_response(request)

    def _ensure_host_allowed(self, host):
        from django.conf import settings

        if self._statically_allowed(host, settings.ALLOWED_HOSTS):
            return

        custom_domains = get_cached_custom_domains()
        if host not in custom_domains:
            return

        if host not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, host]

        origin = f'https://{host}'
        if origin not in settings.CSRF_TRUSTED_ORIGINS:
            settings.CSRF_TRUSTED_ORIGINS = [*settings.CSRF_TRUSTED_ORIGINS, origin]

    @staticmethod
    def _statically_allowed(host, allowed_hosts):
        for entry in allowed_hosts:
            if entry.startswith('.'):
                if host == entry[1:] or host.endswith(entry):
                    return True
            elif host == entry:
                return True
        return False


class UserContextMiddleware:
    """Postavlja trenutnog korisnika u thread-local kontekst za audit log."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user_context.set_current_user(
            request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
        )
        try:
            return self.get_response(request)
        finally:
            user_context.clear_current_user()


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host()
        request.is_platform_admin = is_platform_admin_host(host)
        request.service_host_kind = resolve_service_host(host)

        if request.path.startswith('/api/ready'):
            tenant = None
        elif is_as4_inbound_path(request.path) or request.service_host_kind == ServiceHostKind.AS4:
            from .models import Tenant

            tenant = Tenant.objects.filter(slug='finestar', is_active=True).first()
            if tenant is None:
                raise Http404('Tenant nije pronađen.')
        elif request.service_host_kind == ServiceHostKind.OTP:
            tenant = None
        elif request.is_platform_admin:
            tenant = resolve_platform_tenant(request)
        else:
            tenant = resolve_tenant_from_host(host)
            if tenant is None:
                raise Http404('Tenant nije pronađen.')

        request.tenant = tenant
        context.set_current_tenant(tenant)
        try:
            return self.get_response(request)
        finally:
            context.clear_current_tenant()
