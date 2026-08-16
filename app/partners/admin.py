from django.contrib import admin

from tenants.mixins import TenantAdminMixin

from .models import PartnerType, Partner, PartnerContact, PartnerBankAccount


class PartnerContactInline(admin.TabularInline):
    model = PartnerContact
    extra = 1
    fields = (
        'contact_type', 'first_name', 'last_name',
        'position', 'department',
        'email', 'phone', 'mobile',
        'is_primary', 'is_active'
    )
    show_change_link = True


class PartnerBankAccountInline(admin.TabularInline):
    model = PartnerBankAccount
    extra = 1
    fields = (
        'bank_name', 'bic', 'iban', 
        'currency', 'is_primary', 'is_active'
    )
    show_change_link = True


@admin.register(Partner)
class PartnerAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'partner_code', 'name', 'partner_type', 'status',
        'tax_number', 'city', 'country', 'created_at'
    )
    list_filter = ('partner_type', 'status', 'city', 'country')
    search_fields = ('partner_code', 'name', 'tax_number', 'vat_number', 'registration_number')
    readonly_fields = ('partner_code', 'created_at', 'updated_at')
    inlines = [PartnerContactInline, PartnerBankAccountInline]


@admin.register(PartnerType)
class PartnerTypeAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'description', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'description')


@admin.register(PartnerContact)
class PartnerContactAdmin(TenantAdminMixin, admin.ModelAdmin):
    tenant_lookup = 'partner__tenant'

    list_display = ('partner', 'full_name', 'contact_type', 'email', 'phone', 'is_primary', 'is_active')
    list_filter = ('contact_type', 'is_active', 'is_primary')
    search_fields = ('first_name', 'last_name', 'email', 'partner__name')


@admin.register(PartnerBankAccount)
class PartnerBankAccountAdmin(TenantAdminMixin, admin.ModelAdmin):
    tenant_lookup = 'partner__tenant'

    list_display = ('partner', 'bank_name', 'iban', 'bic', 'currency', 'is_primary', 'is_active')
    list_filter = ('is_active', 'is_primary', 'currency')
    search_fields = ('bank_name', 'iban', 'bic', 'partner__name')
