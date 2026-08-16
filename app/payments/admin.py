from django.contrib import admin

from tenants.mixins import TenantAdminMixin

from .models import BankAccount, Payment


@admin.register(BankAccount)
class BankAccountAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('account_name', 'bank_name', 'account_number', 'currency', 'ledger_account', 'is_active')
    list_filter = ('bank_name', 'currency', 'is_active')
    search_fields = ('account_name', 'account_number', 'iban')
    autocomplete_fields = ('ledger_account',)


@admin.register(Payment)
class PaymentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'payment_number', 'payment_type', 'payment_method', 'status',
        'amount', 'currency', 'payment_date', 'counterparty_name',
    )
    list_filter = ('payment_type', 'payment_method', 'status', 'currency', 'payment_date')
    search_fields = ('payment_number', 'description', 'counterparty_name', 'reference_number')
    date_hierarchy = 'payment_date'
    
    fieldsets = (
        ('Osnovni podaci', {
            'fields': ('payment_number', 'payment_type', 'payment_method', 'status')
        }),
        ('Financijski podaci', {
            'fields': ('amount', 'currency', 'bank_account', 'related_invoice')
        }),
        ('Datumi', {
            'fields': ('payment_date', 'value_date')
        }),
        ('Druga strana', {
            'fields': ('counterparty_name', 'counterparty_account', 'reference_number')
        }),
        ('Dodatne informacije', {
            'fields': ('description',)
        }),
    )
