from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from config.schema_common import ERROR_401
from tenants.models import Tenant, TenantMembership
from tenants.schema import (
    AuthMeResponseSerializer,
    TokenObtainRequestSerializer,
    TokenPairResponseSerializer,
    TokenRefreshRequestSerializer,
)
from tenants.serializers import TurnstileTokenObtainPairSerializer


def _platform_admin_host() -> str:
    admin_hosts = getattr(settings, 'TENANT_PLATFORM_ADMIN_HOSTS', [])
    if admin_hosts:
        return admin_hosts[0]
    platform_domain = getattr(settings, 'TENANT_PLATFORM_DOMAIN', 'racunai.hr')
    return f'admin.{platform_domain}'


def _tenant_admin_url(tenant: Tenant) -> str:
    if tenant.custom_domain:
        return f'https://{tenant.custom_domain}/admin/'
    platform_domain = getattr(settings, 'TENANT_PLATFORM_DOMAIN', 'racunai.hr')
    infix = getattr(settings, 'TENANT_STAGE_INFIX', '') or ''
    return f'https://{tenant.slug}{infix}.{platform_domain}/admin/'


def _serialize_tenant_membership(membership: TenantMembership) -> dict:
    tenant = membership.tenant
    return {
        'slug': tenant.slug,
        'name': tenant.name,
        'role': membership.role,
        'is_default': membership.is_default,
        'admin_url': _tenant_admin_url(tenant),
    }


class AuthTokenObtainPairView(TokenObtainPairView):
    permission_classes = [AllowAny]
    serializer_class = TurnstileTokenObtainPairSerializer

    @extend_schema(
        tags=['auth'],
        operation_id='auth_token_obtain',
        request=TokenObtainRequestSerializer,
        responses={200: TokenPairResponseSerializer, 401: ERROR_401},
        auth=[],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class AuthTokenRefreshView(TokenRefreshView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['auth'],
        operation_id='auth_token_refresh',
        request=TokenRefreshRequestSerializer,
        responses={200: TokenPairResponseSerializer, 401: ERROR_401},
        auth=[],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class AuthMeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['auth'],
        operation_id='auth_me_retrieve',
        responses={200: AuthMeResponseSerializer, 401: ERROR_401},
    )
    def get(self, request):
        user = request.user

        tenants = []
        if user.is_superuser:
            for tenant in Tenant.objects.filter(is_active=True).order_by('name'):
                tenants.append({
                    'slug': tenant.slug,
                    'name': tenant.name,
                    'role': 'owner',
                    'is_default': False,
                    'admin_url': _tenant_admin_url(tenant),
                })
        else:
            memberships = (
                TenantMembership.objects.filter(user=user, tenant__is_active=True)
                .select_related('tenant')
                .order_by('-is_default', 'tenant__name')
            )
            tenants = [_serialize_tenant_membership(m) for m in memberships]

        return Response({
            'user': {
                'id': user.pk,
                'username': user.username,
                'email': user.email,
                'is_superuser': user.is_superuser,
            },
            'tenants': tenants,
            'platform_admin_url': f'https://{_platform_admin_host()}/admin/',
        })
