from django.urls import path, include
from rest_framework.routers import DefaultRouter

from config.health import ready_view
from tenants.api_views import AuthMeView, AuthTokenObtainPairView, AuthTokenRefreshView

# API Router
router = DefaultRouter()

# Registriraj svoje API viewove ovdje
# router.register(r'users', UserViewSet)

urlpatterns = [
    path('ready/', ready_view, name='api_ready'),
    path('', include(router.urls)),
    path('fiscal/', include('fiscal_gateway.api.urls')),
    path('', include('domains.reporting.api.urls')),
    path('', include('domains.banking.api.urls')),
    path('', include('domains.partners.api.urls')),
    path('', include('domains.finance.api.urls')),
    path('auth/token/', AuthTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', AuthTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/me/', AuthMeView.as_view(), name='auth_me'),
    path('auth/', include('rest_framework.urls')),
]

try:
    from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
except ImportError:  # pragma: no cover - celery image without schema deps
    pass
else:
    urlpatterns = [
        path('schema/', SpectacularAPIView.as_view(), name='api-schema'),
        path(
            'schema/swagger-ui/',
            SpectacularSwaggerView.as_view(url_name='api-schema'),
            name='api-schema-swagger-ui',
        ),
        path(
            'schema/redoc/',
            SpectacularRedocView.as_view(url_name='api-schema'),
            name='api-schema-redoc',
        ),
        *urlpatterns,
    ]
