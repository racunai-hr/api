from django.contrib import admin
from django.utils.html import format_html

from tenants.mixins import TenantAdminMixin

from .models import TaxRate, TaxOffice, CompanySettings, ResponsiblePerson, SystemParameter


class ResponsiblePersonInline(admin.TabularInline):
    model = ResponsiblePerson
    extra = 1
    fields = ('title', 'first_name', 'last_name', 'email', 'phone', 'is_primary', 'is_active', 'notes')


@admin.register(TaxOffice)
class TaxOfficeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'city')
    search_fields = ('code', 'name', 'city')
    ordering = ('code',)


@admin.register(TaxRate)
class TaxRateAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'rate_formatted', 'is_default', 'is_active', 'created_at')
    list_filter = ('is_default', 'is_active', 'created_at')
    search_fields = ('name', 'description')
    list_editable = ('is_active',)
    ordering = ('rate',)
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        (None, {
            'fields': ('name', 'rate', 'description')
        }),
        ('Postavke', {
            'fields': ('is_default', 'is_active')
        }),
        ('Audit', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def rate_formatted(self, obj):
        return f"{obj.rate}%"
    rate_formatted.short_description = "Stopa"
    rate_formatted.admin_order_field = 'rate'


@admin.register(CompanySettings)
class CompanySettingsAdmin(TenantAdminMixin, admin.ModelAdmin):
    inlines = [ResponsiblePersonInline]
    
    fieldsets = (
        ('Osnovni podaci tvrtke', {
            'fields': (
                'company_name',
                ('street', 'house_number'),
                ('postal_code', 'city', 'country'),
                'company_address',
                'company_phone',
                'company_email',
                'company_website',
            ),
        }),
        ('Logo', {
            'fields': ('company_logo', 'logo_preview'),
            'classes': ('collapse',)
        }),
        ('Porezni podaci', {
            'fields': ('vat_number', 'tax_number', 'registration_number', 'tax_office')
        }),
        ('Financijske postavke', {
            'fields': ('default_currency', 'default_tax_rate')
        }),
        ('Ostale postavke', {
            'fields': ('timezone', 'date_format')
        }),
        ('Audit', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at', 'logo_preview')
    
    def logo_preview(self, obj):
        if obj.company_logo:
            return format_html(
                '<img src="{}" width="100" height="100" style="object-fit: contain; border: 1px solid #ddd;" />',
                obj.company_logo.url
            )
        return "Nema loga"
    logo_preview.short_description = "Pregled loga"


@admin.register(ResponsiblePerson)
class ResponsiblePersonAdmin(TenantAdminMixin, admin.ModelAdmin):
    tenant_lookup = 'company_settings__tenant'

    list_display = ('full_name_display', 'title_display', 'company_settings', 'email', 'phone', 'is_primary', 'is_active')
    list_filter = ('title', 'is_primary', 'is_active', 'company_settings', 'created_at')
    search_fields = ('first_name', 'last_name', 'email', 'phone')
    list_editable = ('is_active',)
    ordering = ('company_settings', 'last_name', 'first_name')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Osnovni podaci', {
            'fields': ('company_settings', 'title', 'first_name', 'last_name')
        }),
        ('Kontakt podaci', {
            'fields': ('email', 'phone')
        }),
        ('Postavke', {
            'fields': ('is_primary', 'is_active', 'notes')
        }),
        ('Audit', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def full_name_display(self, obj):
        return obj.full_name
    full_name_display.short_description = "Ime i prezime"
    full_name_display.admin_order_field = 'last_name'
    
    def title_display(self, obj):
        return obj.get_title_display()
    title_display.short_description = "Funkcija"
    title_display.admin_order_field = 'title'


@admin.register(SystemParameter)
class SystemParameterAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('key', 'value_short', 'data_type')
    list_filter = ('data_type',)
    search_fields = ('key', 'description', 'value')
    ordering = ('key',)
    
    fields = ('key', 'value', 'description', 'data_type')
    
    def value_short(self, obj):
        value = str(obj.value)
        if len(value) > 50:
            return value[:47] + "..."
        return value
    value_short.short_description = "Vrijednost"


from tenants.branding import apply_admin_site_branding

apply_admin_site_branding()
