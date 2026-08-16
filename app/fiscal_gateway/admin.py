from django.contrib import admin, messages
from django.utils import timezone

from fiscal_gateway.client.certificate import CertificateError, load_p12
from fiscal_gateway.client.cis_client import CISClient, CISClientError
from fiscal_gateway.config_utils import (
    FiscalConfigError,
    get_active_fiscal_config,
    resolve_p12_password,
    resolve_p12_path,
)
from tenants.mixins import TenantAdminMixin

from .models import As4DocumentLink, DirectTenantConfig, FiscalSubmissionLog, FiscalTenantConfig


@admin.register(FiscalTenantConfig)
class FiscalTenantConfigAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('tenant', 'oib', 'cis_env', 'certificate_label', 'is_active', 'last_ping_at')
    list_filter = ('is_active', 'cis_env')
    search_fields = ('tenant__slug', 'tenant__name', 'oib', 'certificate_label')
    readonly_fields = ('last_ping_at', 'last_submission_at')
    actions = ('test_fiscal_certificate',)

    fieldsets = (
        (None, {
            'fields': ('oib', 'cis_env', 'certificate_label', 'is_active'),
        }),
        ('Certifikat', {
            'fields': ('p12_path', 'p12_password'),
            'description': 'Prazna polja koriste FISCAL_CERT_* varijable okoline.',
        }),
        ('Status', {
            'fields': ('last_ping_at', 'last_submission_at'),
        }),
    )

    @admin.action(description='Testiraj certifikat (učitavanje + CIS ping)')
    def test_fiscal_certificate(self, request, queryset):
        for config in queryset:
            try:
                material = load_p12(
                    resolve_p12_path(config),
                    resolve_p12_password(config),
                )
                client = CISClient(config)
                client.ping()
            except (FiscalConfigError, CertificateError, CISClientError) as exc:
                self.message_user(
                    request,
                    f'{config.tenant.slug}: {exc}',
                    level=messages.ERROR,
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f'{config.tenant.slug}: {exc}',
                    level=messages.ERROR,
                )
            else:
                config.last_ping_at = timezone.now()
                config.save(update_fields=['last_ping_at'])
                self.message_user(
                    request,
                    f'{config.tenant.slug}: certifikat OK ({material.subject}), CIS dostupan.',
                    level=messages.SUCCESS,
                )


@admin.register(FiscalSubmissionLog)
class FiscalSubmissionLogAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('operation', 'status', 'jir', 'error_code', 'cis_request_id', 'created_at')
    list_filter = ('status', 'operation')
    search_fields = ('request_id', 'jir', 'cis_request_id', 'error_code')
    readonly_fields = (
        'request_id',
        'xml_hash',
        'request_xml',
        'response_xml',
        'created_at',
    )


@admin.register(DirectTenantConfig)
class DirectTenantConfigAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('tenant', 'oib', 'ap_party_id', 'is_active', 'last_outbound_at', 'last_inbound_at')
    list_filter = ('is_active',)
    search_fields = ('tenant__slug', 'tenant__name', 'oib')
    readonly_fields = ('last_outbound_at', 'last_inbound_at')


@admin.register(As4DocumentLink)
class As4DocumentLinkAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'message_id',
        'direction',
        'as4_status',
        'recipient_oib',
        'supplier_oib',
        'created_at',
    )
    list_filter = ('direction', 'as4_status')
    search_fields = ('message_id', 'recipient_oib', 'supplier_oib')
    readonly_fields = ('created_at', 'last_synced_at')
