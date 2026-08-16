from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from fiscal_gateway.client.cis_client import CISClientError
from integrations.admin_display import invoice_integration_status
from integrations.errors import IntegrationError
from integrations.constants import IntegrationEnvironment
from integrations.services.invoice_integration import InvoiceIntegrationService
from ubl.domain.errors import UblError
from accounting.admin_subledger import SubledgerSourceInline
from tenants.mixins import TenantAdminMixin

from .models import Invoice, InvoiceItem


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 1
    fields = ('item_name', 'description', 'quantity', 'unit_price', 'tax_rate', 'line_total')
    readonly_fields = ('line_total',)


@admin.register(Invoice)
class InvoiceAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'invoice_number', 'company_to', 'responsible_person_display',
        'status', 'issue_date', 'due_date', 'total_amount', 'pdf_link',
    )
    list_filter = ('status', 'issue_date', 'due_date', 'responsible_person')
    search_fields = ('invoice_number', 'company_to__name', 'description')
    readonly_fields = (
        'subtotal', 'tax_amount', 'total_amount', 'created_at', 'updated_at',
        'pdf_link', 'integration_status',
    )
    inlines = [InvoiceItemInline, SubledgerSourceInline]
    actions = ['send_eracun_action', 'send_eracun_test_action']

    fieldsets = (
        ('Osnovni podaci', {
            'fields': ('invoice_number', 'status', 'company_to', 'responsible_person', 'pdf_link')
        }),
        ('Datumi', {
            'fields': ('issue_date', 'due_date', 'service_date')
        }),
        ('Financijski podaci', {
            'fields': ('subtotal', 'tax_amount', 'total_amount'),
            'classes': ('collapse',)
        }),
        ('Dodatne informacije', {
            'fields': ('description', 'notes')
        }),
        ('Integracija (eRačun / fiskalizacija)', {
            'fields': ('integration_status',),
        }),
        ('Audit', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description='Integracija')
    def integration_status(self, obj):
        return invoice_integration_status(obj)

    @admin.display(description='PDF')
    def pdf_link(self, obj):
        if not obj.pk:
            return '-'
        url = reverse('invoices:invoice_pdf', args=[obj.pk])
        return format_html('<a href="{}" target="_blank">Preuzmi PDF</a>', url)

    def responsible_person_display(self, obj):
        if obj.responsible_person:
            return f"{obj.responsible_person.full_name} ({obj.responsible_person.get_title_display()})"
        return "-"
    responsible_person_display.short_description = "Odgovorna osoba"
    responsible_person_display.admin_order_field = 'responsible_person__last_name'

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "responsible_person":
            kwargs["queryset"] = db_field.related_model.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description='Pošalji eRačun')
    def send_eracun_action(self, request, queryset):
        self._send_eracun_queryset(request, queryset, IntegrationEnvironment.PRODUCTION)

    @admin.action(description='Pošalji eRačun (PTS test okruženje)')
    def send_eracun_test_action(self, request, queryset):
        self._send_eracun_queryset(request, queryset, IntegrationEnvironment.TEST)

    def _send_eracun_queryset(self, request, queryset, environment):
        sent = 0
        for invoice in queryset:
            try:
                InvoiceIntegrationService.send_eracun(
                    invoice,
                    triggered_by=request.user,
                    environment=environment,
                )
                sent += 1
            except (UblError, IntegrationError, CISClientError) as exc:
                detail = '; '.join(getattr(exc, 'messages', []) or [str(exc)])
                self.message_user(request, f'{invoice}: {detail}', level=messages.ERROR)
        if sent:
            env_label = 'test' if environment == IntegrationEnvironment.TEST else 'produkcija'
            self.message_user(
                request,
                f'Poslano {sent} eRačuna ({env_label}).',
                level=messages.SUCCESS,
            )


@admin.register(InvoiceItem)
class InvoiceItemAdmin(TenantAdminMixin, admin.ModelAdmin):
    tenant_lookup = 'invoice__tenant'

    list_display = ('invoice', 'item_name', 'quantity', 'unit_price', 'tax_rate', 'line_total')
    list_filter = ('invoice__status', 'tax_rate')
    search_fields = ('item_name', 'description', 'invoice__invoice_number')
    readonly_fields = ('line_total',)
