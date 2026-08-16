from django.contrib import admin

from tenants.mixins import TenantAdminMixin

from .models import Notification, KPIMetric


@admin.register(Notification)
class NotificationAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('title', 'user', 'priority', 'is_read', 'created_at')
    list_filter = ('priority', 'is_read', 'created_at')
    search_fields = ('title', 'message')
    verbose_name = "Obavijest"
    verbose_name_plural = "Obavijesti"


@admin.register(KPIMetric)
class KPIMetricAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'value', 'target_value', 'unit', 'category', 'date')
    list_filter = ('category', 'date')
    search_fields = ('name', 'category')
    verbose_name = "KPI metrika"
    verbose_name_plural = "KPI metrike"
