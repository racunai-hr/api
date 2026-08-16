from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve

from tenants.views import switch_tenant

urlpatterns = [
    path('admin/switch-tenant/', switch_tenant, name='admin_switch_tenant'),
    path('admin/', admin.site.urls),
    path('api/', include('config.api_urls')),
    path('', include('banking.urls')),
    path("invoices/", include("invoices.urls")),
    path('expenses/', include('expenses.urls')),
    path('accounting/', include('accounting.urls')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
else:
    urlpatterns += [
        re_path(
            r'^media/(?P<path>.*)$',
            serve,
            {'document_root': settings.MEDIA_ROOT},
        ),
    ]