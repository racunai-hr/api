"""Centralizirani admin branding za racunAI SaaS platformu."""

from django.conf import settings
from django.templatetags.static import static


def platform_domain() -> str:
    return getattr(settings, 'TENANT_PLATFORM_DOMAIN', 'racunai.hr')


SITE_HEADER = 'racunAI'
SITE_TITLE = 'racunAI'
INDEX_TITLE = 'Dobrodošli u racunAI admin'
LOGO_STATIC_PATH = 'branding/racunai-logo.png'


def apply_admin_site_branding():
    from django.contrib import admin

    from tenants.admin_forms import TurnstileAdminAuthenticationForm

    admin.site.site_header = SITE_HEADER
    admin.site.site_title = SITE_TITLE
    admin.site.index_title = INDEX_TITLE
    admin.site.login_form = TurnstileAdminAuthenticationForm


def admin_branding_context(request):
    """Kontekst za admin predloške (base_site.html)."""
    tenant = getattr(request, 'tenant', None)
    tenant_name = None
    if tenant and not getattr(request, 'is_platform_admin', False):
        tenant_name = tenant.name

    turnstile_site_key = getattr(settings, 'TURNSTILE_SITE_KEY', '')
    turnstile_active = False
    if request is not None:
        from tenants.turnstile import turnstile_required_for_request
        turnstile_active = turnstile_required_for_request(request)

    return {
        'admin_site_header': SITE_HEADER,
        'admin_site_title': SITE_TITLE,
        'admin_index_title': INDEX_TITLE,
        'admin_platform_domain': platform_domain(),
        'admin_tenant_name': tenant_name,
        'admin_logo_url': static(LOGO_STATIC_PATH),
        'turnstile_enabled': turnstile_active,
        'turnstile_site_key': turnstile_site_key if turnstile_active else '',
    }
