from django.conf import settings
from django.core.cache import cache

from .models import Tenant, TenantMembership

CUSTOM_DOMAIN_CACHE_KEY = 'tenant_custom_domains'
CUSTOM_DOMAIN_CACHE_TTL = 60


def normalize_host(host):
    return host.split(':')[0].lower()


def get_cached_custom_domains():
    cached = cache.get(CUSTOM_DOMAIN_CACHE_KEY)
    if cached is not None:
        return cached
    domains = frozenset(
        Tenant.objects.filter(is_active=True)
        .exclude(custom_domain__isnull=True)
        .exclude(custom_domain='')
        .values_list('custom_domain', flat=True)
    )
    normalized = frozenset(normalize_host(d) for d in domains)
    cache.set(CUSTOM_DOMAIN_CACHE_KEY, normalized, CUSTOM_DOMAIN_CACHE_TTL)
    return normalized


def invalidate_custom_domain_cache():
    cache.delete(CUSTOM_DOMAIN_CACHE_KEY)


def get_reserved_slugs():
    return frozenset(getattr(settings, 'TENANT_RESERVED_SLUGS', []))


def is_platform_admin_host(host):
    host = normalize_host(host)
    admin_hosts = getattr(settings, 'TENANT_PLATFORM_ADMIN_HOSTS', [])
    return host in admin_hosts


def resolve_tenant_from_host(host):
    """Resolve active tenant from HTTP Host header."""
    host = normalize_host(host)
    platform_domain = getattr(settings, 'TENANT_PLATFORM_DOMAIN', 'racunai.hr')
    legacy_host_map = getattr(settings, 'TENANT_LEGACY_HOST_MAP', {})
    reserved_slugs = get_reserved_slugs()

    slug = legacy_host_map.get(host)
    if slug:
        return Tenant.objects.filter(slug=slug, is_active=True).first()

    tenant = Tenant.objects.filter(custom_domain=host, is_active=True).first()
    if tenant:
        return tenant

    suffix = f'.{platform_domain}'
    if host.endswith(suffix):
        slug = host[: -len(suffix)]
        if slug and '.' not in slug and slug not in reserved_slugs:
            return Tenant.objects.filter(slug=slug, is_active=True).first()

    default_slug = getattr(settings, 'TENANT_DEFAULT_SLUG', '')
    if default_slug and settings.DEBUG:
        return Tenant.objects.filter(slug=default_slug, is_active=True).first()

    return None


def resolve_platform_tenant(request):
    """Pick tenant on admin.racunai.hr from session or user membership."""
    session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_tenant_id')
    session = getattr(request, 'session', None)
    if session is not None:
        session_tenant_id = session.get(session_key)
        if session_tenant_id:
            tenant = Tenant.objects.filter(pk=session_tenant_id, is_active=True).first()
            if tenant:
                return tenant

    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None

    if user.is_superuser:
        return None

    membership = (
        TenantMembership.objects.filter(user=user, is_default=True)
        .select_related('tenant')
        .first()
    )
    if membership and membership.tenant.is_active:
        return membership.tenant

    membership = (
        TenantMembership.objects.filter(user=user, tenant__is_active=True)
        .select_related('tenant')
        .first()
    )
    if membership:
        return membership.tenant

    return None
