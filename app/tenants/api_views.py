from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from tenants.models import Tenant, TenantMembership
from tenants.serializers import TurnstileTokenObtainPairSerializer


def _tenant_admin_url(tenant: Tenant) -> str:
    if tenant.custom_domain:
        return f'https://{tenant.custom_domain}/admin/'
    platform_domain = getattr(settings, 'TENANT_PLATFORM_DOMAIN', 'racunai.hr')
    return f'https://{tenant.slug}.{platform_domain}/admin/'


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


class AuthTokenRefreshView(TokenRefreshView):
    permission_classes = [AllowAny]


class AuthMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        platform_domain = getattr(settings, 'TENANT_PLATFORM_DOMAIN', 'racunai.hr')

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
            'platform_admin_url': f'https://admin.{platform_domain}/admin/',
        })
