from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from tenants.models import Tenant
from tenants.permissions import role_can_export, role_can_view, get_role


def require_tenant_export(view_func):
    """Dekorator za accounting export viewove — viewer+ može preuzimati."""

    @login_required
    def wrapper(request, *args, **kwargs):
        tenant = getattr(request, 'tenant', None)
        if tenant is None and not request.user.is_superuser:
            return HttpResponseForbidden('Tenant nije određen.')
        role = get_role(request.user, tenant)
        if not request.user.is_superuser and not role_can_export(role):
            return HttpResponseForbidden('Nemate dozvolu za izvoz izvještaja.')
        return view_func(request, *args, **kwargs)

    return wrapper


@staff_member_required
@require_POST
def switch_tenant(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Samo superuser može mijenjati tenant.')

    from django.conf import settings

    tenant_id = request.POST.get('tenant_id', '')
    session_key = getattr(settings, 'TENANT_SESSION_KEY', 'active_tenant_id')

    if tenant_id == '' or tenant_id == 'all':
        request.session.pop(session_key, None)
    else:
        tenant = Tenant.objects.filter(pk=tenant_id, is_active=True).first()
        if tenant is None:
            return HttpResponseForbidden('Tenant nije pronađen.')
        request.session[session_key] = tenant.pk

    next_url = request.POST.get('next', reverse('admin:index'))
    return HttpResponseRedirect(next_url)
