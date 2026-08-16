from django.contrib import admin, messages

from tenants.mixins import TenantAdminMixin

from .client import SuperAPIError, SuperClient
from .forms import SuperTenantConfigForm
from .models import SuperDocumentLink, SuperTenantConfig


@admin.register(SuperTenantConfig)
class SuperTenantConfigAdmin(TenantAdminMixin, admin.ModelAdmin):
    form = SuperTenantConfigForm
    list_display = (
        'tenant',
        'username',
        'api_base_url',
        'company_guid',
        'is_active',
        'is_test_mode',
        'last_inbound_sync_at',
    )
    list_filter = ('is_active', 'is_test_mode')
    search_fields = ('tenant__slug', 'tenant__name', 'username', 'company_guid')
    readonly_fields = (
        'api_base_url',
        'is_test_mode',
        'last_inbound_sync_at',
        'last_outbound_poll_at',
    )
    actions = ('test_super_connection',)

    fieldsets = (
        ('Okruženje (deprecated)', {
            'description': 'API URL i test/prod okruženje sada se određuju iz IntegrationConfig (integrations).',
            'fields': ('api_base_url', 'is_test_mode'),
        }),
        ('SUPER credentials', {
            'fields': ('username', 'password', 'company_guid', 'is_active'),
        }),
        ('Sync', {
            'fields': ('last_inbound_sync_at', 'last_outbound_poll_at'),
        }),
    )

    @admin.action(description='Testiraj SUPER vezu')
    def test_super_connection(self, request, queryset):
        for config in queryset:
            try:
                client = SuperClient(config)
                client.get_token(force_refresh=True)
            except SuperAPIError as exc:
                self.message_user(
                    request,
                    f'{config.tenant.slug}: SUPER greška — {exc}',
                    level=messages.ERROR,
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f'{config.tenant.slug}: {exc}',
                    level=messages.ERROR,
                )
            else:
                self.message_user(
                    request,
                    f'{config.tenant.slug}: SUPER veza uspješna (token dobiven).',
                    level=messages.SUCCESS,
                )


@admin.register(SuperDocumentLink)
class SuperDocumentLinkAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('direction', 'super_guid', 'super_status', 'content_type', 'object_id', 'last_synced_at')
    list_filter = ('direction', 'super_status')
    search_fields = ('super_guid', 'super_unique_id')
    readonly_fields = ('ubl_xml', 'pdf_path', 'created_at', 'last_synced_at')
