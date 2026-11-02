from django.contrib import admin, messages

from fiscal_gateway.client.gateway_v1_client import GatewayV1Client, GatewayV1Error
from settings.models import CompanySettings
from tenants.mixins import TenantAdminMixin

from .forms import SuperTenantConfigForm
from .models import SuperDocumentLink, SuperTenantConfig


def _tenant_oib(tenant) -> str | None:
    company = CompanySettings.all_objects.filter(tenant=tenant).first()
    if company is None:
        return None
    raw = (company.tax_number or company.vat_number or '').strip().upper()
    if raw.startswith('HR'):
        raw = raw[2:]
    digits = ''.join(ch for ch in raw if ch.isdigit())
    return digits if len(digits) == 11 else None


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
    actions = ('test_gateway_connection',)

    fieldsets = (
        ('Okruženje (deprecated)', {
            'description': 'API URL i test/prod okruženje sada se određuju iz IntegrationConfig (integrations).',
            'fields': ('api_base_url', 'is_test_mode'),
        }),
        ('SUPER credentials (legacy read-only)', {
            'description': 'M1.7: racunai-api ne zove SUPER API. Credentiali su historijski zapis.',
            'fields': ('username', 'password', 'company_guid', 'is_active'),
        }),
        ('Sync', {
            'fields': ('last_inbound_sync_at', 'last_outbound_poll_at'),
        }),
    )

    @admin.action(description='Testiraj fiscal gateway vezu')
    def test_gateway_connection(self, request, queryset):
        for config in queryset:
            oib = _tenant_oib(config.tenant)
            if not oib:
                self.message_user(
                    request,
                    f'{config.tenant.slug}: nedostaje OIB u postavkama tvrtke.',
                    level=messages.ERROR,
                )
                continue
            try:
                client = GatewayV1Client(taxpayer_oib=oib, timeout=10)
                caps = client.provider_capabilities('super', taxpayer_oib=oib)
            except GatewayV1Error as exc:
                self.message_user(
                    request,
                    f'{config.tenant.slug}: gateway greška — {exc}',
                    level=messages.ERROR,
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f'{config.tenant.slug}: {exc}',
                    level=messages.ERROR,
                )
            else:
                readiness = caps.get('outbound_readiness') or {}
                inbound = caps.get('inbound_readiness') or {}
                ready = readiness.get('ready') or inbound.get('active_binding')
                level = messages.SUCCESS if ready else messages.WARNING
                self.message_user(
                    request,
                    (
                        f'{config.tenant.slug}: gateway odgovor primljen '
                        f'(outbound_ready={readiness.get("ready")}, '
                        f'inbound_binding={inbound.get("active_binding")}).'
                    ),
                    level=level,
                )


@admin.register(SuperDocumentLink)
class SuperDocumentLinkAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('direction', 'super_guid', 'super_status', 'content_type', 'object_id', 'last_synced_at')
    list_filter = ('direction', 'super_status')
    search_fields = ('super_guid', 'super_unique_id')
    readonly_fields = ('ubl_xml', 'pdf_path', 'created_at', 'last_synced_at')
