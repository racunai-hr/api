from __future__ import annotations

from functools import wraps
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_POST

from banking.otp.client import OtpClientError
from banking.provider_models import BankConnection, PaymentOrder
from banking.services.pis_orders import (
    handle_payment_sca_callback,
    refresh_payment_order_status,
    resolve_pending_payment_order,
)
from banking.services.connect import (
    handle_oauth_callback,
    handle_sca_callback,
    list_linked_accounts,
    start_connect_flow,
)
from banking.services.payments import start_payment_initiation
from payments.models import BankAccount, Payment
from tenants import context
from tenants.models import Tenant
from tenants.service_hosts import is_otp_service_host


def _resolve_tenant_from_request(request) -> Tenant:
    tenant = getattr(request, 'tenant', None)
    if tenant is not None:
        return tenant
    tenant_slug = request.GET.get('tenant_slug')
    if tenant_slug:
        return get_object_or_404(Tenant, slug=tenant_slug, is_active=True)
    raise Http404('Tenant nije pronađen.')


def _client_ip(request) -> str:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


def _platform_login_url() -> str:
    return getattr(settings, 'PLATFORM_ADMIN_LOGIN_URL', 'https://admin.racunai.hr/admin/login/')


def _tenant_admin_base(tenant: Tenant) -> str:
    if tenant.custom_domain:
        return f'https://{tenant.custom_domain}'
    platform_domain = getattr(settings, 'TENANT_PLATFORM_DOMAIN', 'racunai.hr')
    return f'https://{tenant.slug}.{platform_domain}'


def _bank_connection_admin_url(connection: BankConnection) -> str:
    return (
        f'{_tenant_admin_base(connection.tenant)}'
        f'/admin/banking/bankconnection/{connection.pk}/change/'
    )


def platform_login_required(view_func):
    """Preusmjeri neprijavljene korisnike na platform admin login s punim return URL-om."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            return view_func(request, *args, **kwargs)
        return redirect_to_login(
            request.build_absolute_uri(),
            _platform_login_url(),
            redirect_field_name='next',
        )

    return wrapper


@platform_login_required
@require_GET
def connect_view(request):
    if not is_otp_service_host(request.get_host()) and request.tenant is None:
        raise Http404()

    tenant = _resolve_tenant_from_request(request)
    bank_account_id = request.GET.get('bank_account_id')
    if not bank_account_id:
        return HttpResponse('bank_account_id je obavezan.', status=400)

    bank_account = get_object_or_404(
        BankAccount.all_objects,
        pk=bank_account_id,
        tenant=tenant,
    )
    context.set_current_tenant(tenant)
    try:
        try:
            _, redirect_url = start_connect_flow(
                tenant=tenant,
                bank_account=bank_account,
                user_ip=_client_ip(request),
            )
        except OtpClientError as exc:
            return HttpResponse(f'OTP connect greška: {exc}', status=502)
    finally:
        context.clear_current_tenant()
    return redirect(redirect_url)


@require_GET
def oauth_callback_view(request):
    code = request.GET.get('code')
    state = request.GET.get('state')
    confirmation_code = request.GET.get('confirmationCode')
    scope = request.GET.get('scope', 'OTP.PSD2')

    if confirmation_code:
        if resolve_pending_payment_order() is not None:
            try:
                order = handle_payment_sca_callback(confirmation_code=confirmation_code)
            except Exception as exc:
                return HttpResponse(f'PIS SCA callback neuspješan: {exc}', status=400)
            admin_url = (
                f'{_tenant_admin_base(order.connection.tenant)}'
                f'/admin/banking/paymentorder/{order.pk}/change/'
            )
            return redirect(admin_url)

        try:
            connection = handle_sca_callback(confirmation_code=confirmation_code)
        except Exception as exc:
            return HttpResponse(f'SCA callback neuspješan: {exc}', status=400)

        admin_url = _bank_connection_admin_url(connection)
        return redirect(admin_url)

    if not code or not state:
        return HttpResponse('Nedostaje code, state ili confirmationCode.', status=400)

    try:
        connection, sca_url = handle_oauth_callback(code=code, state=state, scope=scope)
    except Exception as exc:
        return HttpResponse(f'OAuth callback neuspješan: {exc}', status=400)

    if sca_url:
        return redirect(sca_url)

    admin_url = _bank_connection_admin_url(connection)
    return redirect(admin_url)


@login_required
@require_GET
def accounts_view(request):
    tenant = _resolve_tenant_from_request(request)
    connection_id = request.GET.get('connection_id')
    if connection_id:
        connections = BankConnection.all_objects.filter(pk=connection_id, tenant=tenant)
    else:
        connections = BankConnection.all_objects.filter(tenant=tenant, status='connected')

    results = []
    for connection in connections:
        accounts = list_linked_accounts(connection)
        results.append({
            'connection_id': connection.pk,
            'bank_account': connection.bank_account.iban or connection.bank_account.account_number,
            'accounts': accounts,
        })
    return JsonResponse({'connections': results})


@login_required
@require_POST
def initiate_payment_view(request, payment_id: int):
    tenant = _resolve_tenant_from_request(request)
    payment = get_object_or_404(Payment.all_objects, pk=payment_id, tenant=tenant)
    connection_id = request.POST.get('connection_id')
    connection = get_object_or_404(
        BankConnection.all_objects,
        pk=connection_id,
        tenant=tenant,
        status='connected',
    )
    order = start_payment_initiation(connection, payment, psu_ip=_client_ip(request))
    if order.sca_redirect_url:
        return redirect(order.sca_redirect_url)
    return JsonResponse({'payment_order_id': order.pk, 'status': order.status})


@require_GET
def payment_callback_view(request):
    confirmation_code = request.GET.get('confirmationCode')
    order_id = request.GET.get('payment_order_id')

    if confirmation_code:
        try:
            order = handle_payment_sca_callback(confirmation_code=confirmation_code)
        except Exception as exc:
            return HttpResponse(f'PIS callback neuspješan: {exc}', status=400)
        admin_url = (
            f'{_tenant_admin_base(order.connection.tenant)}'
            f'/admin/banking/paymentorder/{order.pk}/change/'
        )
        return redirect(admin_url)

    if order_id:
        order = get_object_or_404(PaymentOrder.all_objects, pk=order_id)
        refresh_payment_order_status(order)
        admin_url = (
            f'{_tenant_admin_base(order.connection.tenant)}'
            f'/admin/banking/paymentorder/{order.pk}/change/'
        )
        return redirect(admin_url)

    return HttpResponse('Nedostaje payment_order_id ili confirmationCode.', status=400)
