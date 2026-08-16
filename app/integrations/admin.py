import csv

from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html

from tenants.mixins import TenantAdminMixin
from tenants.models import Tenant

from .models import IntegrationAuditLog, IntegrationConfig, IntegrationOutboxMessage
from .services.ops_metrics import correlation_timeline, ops_dashboard_context
from .services.outbox import reset_for_reprocess


class IntegrationConfigAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'tenant',
        'integration_type',
        'provider',
        'environment',
        'is_active',
        'display_name',
    )
    list_filter = ('integration_type', 'provider', 'environment', 'is_active')
    search_fields = ('tenant__slug', 'tenant__name', 'display_name')

    def get_urls(self):
        custom = [
            path(
                'ops/',
                self.admin_site.admin_view(self.ops_dashboard_view),
                name='integrations_ops_dashboard',
            ),
            path(
                'ops/timeline/<uuid:correlation_id>/',
                self.admin_site.admin_view(self.correlation_timeline_view),
                name='integrations_correlation_timeline',
            ),
            path(
                'ops/export/',
                self.admin_site.admin_view(self.ops_export_csv_view),
                name='integrations_ops_export',
            ),
        ]
        return custom + super().get_urls()

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        tenant_id = request.GET.get('tenant')
        if tenant_id and 'tenant' not in initial:
            initial['tenant'] = tenant_id
        return initial

    def _resolve_tenant(self, request):
        tenant_id = request.GET.get('tenant')
        if not tenant_id:
            return None
        return Tenant.objects.filter(pk=tenant_id).first()

    def ops_dashboard_view(self, request):
        days = int(request.GET.get('days', 7))
        if days not in (7, 30):
            days = 7
        tenant = self._resolve_tenant(request)
        context = {
            **self.admin_site.each_context(request),
            'title': 'Integracije — operativni pregled',
            'opts': IntegrationConfig._meta,
            'metrics': ops_dashboard_context(tenant=tenant, days=days),
            'tenants': Tenant.objects.order_by('slug'),
            'selected_tenant': tenant,
            'days': days,
        }
        return render(request, 'admin/integrations/ops_dashboard.html', context)

    def correlation_timeline_view(self, request, correlation_id):
        tenant = self._resolve_tenant(request)
        logs = correlation_timeline(correlation_id)
        if tenant is not None:
            logs = [entry for entry in logs if entry.tenant_id == tenant.pk]
        context = {
            **self.admin_site.each_context(request),
            'title': f'Audit timeline — {correlation_id}',
            'opts': IntegrationAuditLog._meta,
            'correlation_id': correlation_id,
            'logs': logs,
        }
        return render(request, 'admin/integrations/correlation_timeline.html', context)

    def ops_export_csv_view(self, request):
        days = int(request.GET.get('days', 7))
        tenant = self._resolve_tenant(request)
        from django.utils import timezone
        from datetime import timedelta

        since = timezone.now() - timedelta(days=days)
        qs = IntegrationAuditLog.all_objects.filter(created_at__gte=since).order_by('-created_at')
        if tenant is not None:
            qs = qs.filter(tenant=tenant)

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="integration_audit.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'created_at', 'correlation_id', 'tenant', 'step', 'status',
            'provider', 'integration_type', 'invoice', 'detail',
        ])
        for row in qs.select_related('tenant', 'invoice')[:5000]:
            writer.writerow([
                row.created_at.isoformat(),
                str(row.correlation_id),
                row.tenant.slug if row.tenant_id else '',
                row.step,
                row.status,
                row.provider,
                row.integration_type,
                row.invoice.invoice_number if row.invoice_id else '',
                row.detail,
            ])
        return response


@admin.register(IntegrationAuditLog)
class IntegrationAuditLogAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'correlation_id',
        'tenant',
        'invoice',
        'step',
        'status',
        'integration_type',
        'provider',
        'created_at',
    )
    list_filter = ('step', 'status', 'integration_type', 'provider')
    search_fields = ('correlation_id', 'invoice__invoice_number')
    readonly_fields = (
        'correlation_id',
        'tenant',
        'invoice',
        'integration_type',
        'provider',
        'step',
        'status',
        'detail',
        'triggered_by',
        'super_link',
        'fiscal_log',
        'as4_link',
        'created_at',
        'timeline_link',
    )

    @admin.display(description='Timeline')
    def timeline_link(self, obj):
        url = reverse('admin:integrations_correlation_timeline', args=[obj.correlation_id])
        return format_html('<a href="{}">Prikaži timeline</a>', url)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(IntegrationOutboxMessage)
class IntegrationOutboxMessageAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'correlation_id',
        'tenant',
        'operation',
        'provider',
        'status',
        'attempt_count',
        'next_retry_at',
        'updated_at',
    )
    list_filter = ('status', 'operation', 'provider')
    search_fields = ('correlation_id', 'idempotency_key', 'invoice__invoice_number')
    readonly_fields = (
        'correlation_id',
        'idempotency_key',
        'created_at',
        'updated_at',
    )
    actions = ['reprocess_messages', 'mark_resolved']

    @admin.action(description='Ponovi slanje (reset + requeue)')
    def reprocess_messages(self, request, queryset):
        from fiscal_gateway.tasks import process_outbox_message_task

        count = 0
        for message in queryset.exclude(status=IntegrationOutboxMessage.STATUS_SUCCEEDED):
            reset_for_reprocess(message)
            process_outbox_message_task.delay(message.pk)
            count += 1
        self.message_user(request, f'Requeueano {count} poruka.')

    @admin.action(description='Označi riješeno (dead → succeeded)')
    def mark_resolved(self, request, queryset):
        updated = queryset.filter(status=IntegrationOutboxMessage.STATUS_DEAD).update(
            status=IntegrationOutboxMessage.STATUS_SUCCEEDED,
            last_error='',
            next_retry_at=None,
        )
        self.message_user(request, f'Označeno riješeno: {updated}.')


admin.site.register(IntegrationConfig, IntegrationConfigAdmin)
