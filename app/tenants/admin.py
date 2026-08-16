from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from datetime import timedelta
from django.utils import timezone
from django.utils.html import format_html

from integrations.constants import IntegrationProvider, IntegrationType
from integrations.repository import IntegrationRepository

from .models import Tenant, TenantInvitation, TenantMembership


class TenantMembershipInline(admin.TabularInline):
    model = TenantMembership
    extra = 0
    autocomplete_fields = ('user',)


class TenantInvitationInline(admin.TabularInline):
    model = TenantInvitation
    extra = 0
    readonly_fields = ('token', 'status', 'created_at', 'expires_at', 'accepted_at', 'invited_by')
    fields = ('email', 'role', 'status', 'expires_at', 'token', 'invited_by', 'created_at', 'accepted_at')
    show_change_link = True


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'custom_domain', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('slug', 'name', 'custom_domain')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [TenantMembershipInline, TenantInvitationInline]
    change_form_template = 'admin/tenants/tenant/change_form.html'

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        tenant = self.get_object(request, object_id)
        if tenant is not None:
            extra_context['integration_rows'] = self._integration_rows(tenant)
            extra_context['add_integration_url'] = (
                reverse('admin:integrations_integrationconfig_add')
                + f'?tenant={tenant.pk}'
            )
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def _integration_rows(self, tenant):
        rows = []
        for config in IntegrationRepository.list_for_tenant(tenant):
            rows.append({
                'type': config.get_integration_type_display(),
                'provider': config.get_provider_display(),
                'environment': config.get_environment_display(),
                'status': 'Aktivna' if config.is_active else 'Neaktivna',
                'edit_url': reverse(
                    'admin:integrations_integrationconfig_change',
                    args=[config.pk],
                ),
                'provider_url': self._provider_config_url(config),
            })
        return rows

    def _provider_config_url(self, config):
        if (
            config.integration_type == IntegrationType.ERACUN
            and config.provider == IntegrationProvider.DIRECT
        ):
            from fiscal_gateway.models import DirectTenantConfig

            direct_config = DirectTenantConfig.all_objects.filter(tenant=config.tenant).first()
            if direct_config:
                return reverse(
                    'admin:fiscal_gateway_directtenantconfig_change',
                    args=[direct_config.pk],
                )
        if (
            config.integration_type == IntegrationType.ERACUN
            and config.provider == IntegrationProvider.SUPER
        ):
            from super_integration.models import SuperTenantConfig

            super_config = SuperTenantConfig.all_objects.filter(tenant=config.tenant).first()
            if super_config:
                return reverse(
                    'admin:super_integration_supertenantconfig_change',
                    args=[super_config.pk],
                )
        if (
            config.integration_type == IntegrationType.FISCALIZATION
            and config.provider == IntegrationProvider.CIS
        ):
            from fiscal_gateway.models import FiscalTenantConfig

            fiscal_config = FiscalTenantConfig.all_objects.filter(tenant=config.tenant).first()
            if fiscal_config:
                return reverse(
                    'admin:fiscal_gateway_fiscaltenantconfig_change',
                    args=[fiscal_config.pk],
                )
        return None


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'role', 'is_default')
    list_filter = ('role', 'is_default', 'tenant')
    search_fields = ('user__username', 'user__email', 'tenant__name', 'tenant__slug')
    autocomplete_fields = ('user', 'tenant')


@admin.register(TenantInvitation)
class TenantInvitationAdmin(admin.ModelAdmin):
    list_display = ('email', 'tenant', 'role', 'status', 'expires_at', 'invited_by', 'accept_link')
    list_filter = ('status', 'role', 'tenant')
    search_fields = ('email', 'tenant__name', 'tenant__slug')
    readonly_fields = ('token', 'status', 'created_at', 'accepted_at', 'invited_by')
    actions = ['revoke_invitations', 'resend_invitations']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.invited_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='Prihvati')
    def accept_link(self, obj):
        if obj.status != 'pending' or not obj.is_valid:
            return '—'
        url = f'/admin/tenants/tenantinvitation/{obj.pk}/accept/'
        return format_html('<a href="{}">Prihvati pozivnicu</a>', url)

    @admin.action(description='Opozovi odabrane pozivnice')
    def revoke_invitations(self, request, queryset):
        updated = queryset.filter(status='pending').update(status='revoked')
        self.message_user(request, f'Opozvano {updated} pozivnica.')

    @admin.action(description='Obnovi istekle pozivnice (+7 dana)')
    def resend_invitations(self, request, queryset):
        count = 0
        for inv in queryset.filter(status__in=('pending', 'expired')):
            inv.status = 'pending'
            inv.expires_at = timezone.now() + timedelta(days=7)
            inv.save(update_fields=['status', 'expires_at'])
            count += 1
        self.message_user(request, f'Obnovljeno {count} pozivnica.')

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom = [
            path(
                '<path:object_id>/accept/',
                self.admin_site.admin_view(self.accept_invitation_view),
                name='tenants_tenantinvitation_accept',
            ),
        ]
        return custom + urls

    def accept_invitation_view(self, request, object_id):
        invitation = self.get_object(request, object_id)
        changelist = reverse('admin:tenants_tenantinvitation_changelist')

        if invitation is None:
            self.message_user(request, 'Pozivnica nije pronađena.', level=messages.ERROR)
            return HttpResponseRedirect(changelist)

        if not invitation.is_valid:
            self.message_user(request, 'Pozivnica nije valjana ili je istekla.', level=messages.ERROR)
            return HttpResponseRedirect(changelist)

        user = request.user
        if not user.is_authenticated:
            self.message_user(request, 'Morate biti prijavljeni.', level=messages.ERROR)
            return HttpResponseRedirect(changelist)

        if user.email and user.email.lower() != invitation.email.lower():
            self.message_user(
                request,
                f'Pozivnica je za {invitation.email}, vi ste {user.email}.',
                level=messages.ERROR,
            )
            return HttpResponseRedirect(changelist)

        if not user.email:
            user.email = invitation.email
            user.save(update_fields=['email'])

        membership = invitation.accept(user)
        self.message_user(
            request,
            f'Pozivnica prihvaćena — uloga {membership.get_role_display()} za {membership.tenant.name}.',
        )
        return HttpResponseRedirect(changelist)
