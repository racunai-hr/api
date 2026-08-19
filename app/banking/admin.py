from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import path

from tenants.mixins import TenantAdminMixin

from .importers import parse_bank_csv, parse_camt053
from .models import BankImportRun, BankStatement, BankSyncRun, BankTransaction
from .provider_models import (
    BankApiCall,
    BankConnection,
    BankConsent,
    BankProvider,
    BankWebhookEvent,
    PaymentExecution,
    PaymentExecutionTransition,
    PaymentOrder,
    PaymentOrderTransition,
)
from .reconciliation import (
    create_payment_from_transaction,
    match_transaction_to_journal_entry,
    suggest_matches,
    unmatch_transaction,
)
from .services.connect import start_connect_flow
from .services.import_runs import submit_statement_import
from .services.import_statements import import_bank_statement_file
from .services.pis_orders import refresh_payment_order_status, submit_domestic_payment_order
from .tasks import sync_connection_task


class BankTransactionInline(admin.TabularInline):
    model = BankTransaction
    extra = 0
    readonly_fields = ('match_status', 'matched_payment')
    fields = (
        'transaction_date', 'amount', 'transaction_type', 'description',
        'reference', 'counterparty_name', 'match_status', 'matched_payment',
    )


@admin.register(BankProvider)
class BankProviderAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'environment', 'service_host', 'is_active')
    list_filter = ('environment', 'is_active')
    search_fields = ('code', 'name', 'service_host')
    readonly_fields = ('code', 'name', 'environment', 'iam_base', 'api_base', 'service_host')


class BankConsentInline(admin.TabularInline):
    model = BankConsent
    extra = 0
    readonly_fields = (
        'consent_id', 'authorization_id', 'status', 'valid_until',
        'expiry_warning_level', 'expiry_notified_at', 'correlation_id', 'created_at',
    )
    fields = readonly_fields


@admin.register(BankConnection)
class BankConnectionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('bank_provider', 'bank_account', 'status', 'sync_success_streak', 'last_sync_at', 'tenant')
    list_filter = ('status', 'bank_provider')
    search_fields = ('bank_account__iban', 'bank_account__account_name')
    readonly_fields = ('last_sync_at', 'last_error', 'created_at', 'updated_at')
    inlines = [BankConsentInline]
    actions = ['sync_selected_connections', 'connect_otp_bank']

    @admin.action(description='Sinkroniziraj transakcije')
    def sync_selected_connections(self, request, queryset):
        for connection in queryset:
            sync_connection_task.delay(connection.pk)
        self.message_user(request, f'Pokrenuto {queryset.count()} sync zadataka.')

    @admin.action(description='Spoji OTP banku')
    def connect_otp_bank(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, 'Odaberite točno jednu vezu.', level=messages.ERROR)
            return
        connection = queryset.first()
        try:
            _, redirect_url = start_connect_flow(
                tenant=connection.tenant,
                bank_account=connection.bank_account,
            )
        except Exception as exc:
            self.message_user(request, f'Greška: {exc}', level=messages.ERROR)
            return
        return redirect(redirect_url)


@admin.register(BankConsent)
class BankConsentAdmin(TenantAdminMixin, admin.ModelAdmin):
    tenant_lookup = 'connection__tenant'
    list_display = ('consent_id', 'connection', 'status', 'valid_until', 'created_at')
    list_filter = ('status',)
    readonly_fields = ('consent_id', 'authorization_id', 'correlation_id', 'created_at', 'updated_at')


@admin.register(BankApiCall)
class BankApiCallAdmin(admin.ModelAdmin):
    list_display = ('method', 'http_status', 'provider', 'tenant', 'duration_ms', 'created_at')
    list_filter = ('method', 'http_status', 'provider')
    search_fields = ('url', 'request_id', 'error_summary')
    readonly_fields = (
        'tenant', 'provider', 'method', 'url', 'request_id', 'http_status',
        'duration_ms', 'correlation_id', 'error_summary', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BankWebhookEvent)
class BankWebhookEventAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('event_type', 'provider', 'processed_at', 'created_at')
    readonly_fields = ('event_type', 'payload', 'provider', 'processed_at', 'created_at')

    def has_add_permission(self, request):
        return False


class PaymentOrderTransitionInline(admin.TabularInline):
    model = PaymentOrderTransition
    extra = 0
    can_delete = False
    readonly_fields = (
        'sequence', 'from_status', 'to_status', 'actor',
        'reason', 'correlation_id', 'metadata', 'created_at',
    )
    fields = readonly_fields
    ordering = ('sequence',)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PaymentExecutionTransitionInline(admin.TabularInline):
    model = PaymentExecutionTransition
    extra = 0
    can_delete = False
    readonly_fields = (
        'sequence', 'from_status', 'to_status', 'actor',
        'reason', 'correlation_id', 'metadata', 'created_at',
    )
    fields = readonly_fields
    ordering = ('sequence',)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PaymentExecutionInline(admin.TabularInline):
    model = PaymentExecution
    extra = 0
    can_delete = False
    readonly_fields = (
        'attempt', 'status', 'provider_payment_id', 'authorization_id',
        'sca_redirect_url', 'payment_product', 'last_error',
        'correlation_id', 'parent_order_correlation_id',
        'transition_count', 'created_at', 'updated_at',
    )
    fields = readonly_fields
    ordering = ('-attempt',)
    show_change_link = True
    verbose_name = 'Izvršenje'
    verbose_name_plural = 'Izvršenja (detalj + audit prijelaza)'

    @admin.display(description='Prijelazi')
    def transition_count(self, obj: PaymentExecution) -> str:
        if obj.pk is None:
            return '—'
        count = obj.transitions.count()
        return str(count)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentExecution)
class PaymentExecutionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('order', 'attempt', 'status', 'provider_payment_id', 'updated_at')
    list_filter = ('status',)
    search_fields = ('provider_payment_id', 'order__pk')
    readonly_fields = (
        'order', 'attempt', 'status', 'provider_payment_id', 'authorization_id',
        'sca_redirect_url', 'payment_product', 'last_error',
        'correlation_id', 'parent_order_correlation_id',
        'created_at', 'updated_at',
    )
    fieldsets = (
        (None, {
            'fields': readonly_fields,
        }),
    )
    inlines = [PaymentExecutionTransitionInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentOrder)
class PaymentOrderAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'otp_payment_id', 'status', 'amount', 'currency',
        'creditor_iban', 'connection', 'posted_at', 'created_at',
    )
    list_filter = ('status', 'payment_product')
    readonly_fields = (
        'otp_payment_id', 'authorization_id', 'correlation_id',
        'sca_redirect_url', 'last_error',
        'posting_journal_entry', 'posted_at',
        'created_at', 'updated_at',
    )
    fieldsets = (
        (None, {
            'fields': (
                'connection', 'payment', 'payment_product', 'status',
                'otp_payment_id', 'authorization_id', 'sca_redirect_url',
                'debtor_iban', 'creditor_iban', 'creditor_name',
                'amount', 'currency', 'reference', 'correlation_id',
                'last_error', 'created_at', 'updated_at',
            ),
        }),
        ('Knjiženje', {
            'fields': ('posting_journal_entry', 'posted_at'),
        }),
    )
    actions = ['submit_to_bank', 'refresh_status']

    def get_inlines(self, request, obj):
        return [PaymentExecutionInline, PaymentOrderTransitionInline]

    @admin.action(description='Pošalji nalog u OTP (domestic-payment)')
    def submit_to_bank(self, request, queryset):
        submitted = 0
        for order in queryset.filter(status='draft'):
            try:
                submit_domestic_payment_order(order)
                submitted += 1
            except Exception as exc:
                self.message_user(request, f'#{order.pk}: {exc}', level=messages.ERROR)
        self.message_user(request, f'Poslano {submitted} naloga.')

    @admin.action(description='Osvježi status iz OTP-a')
    def refresh_status(self, request, queryset):
        for order in queryset.exclude(otp_payment_id=''):
            refresh_payment_order_status(order)
        self.message_user(request, f'Osvježeno {queryset.count()} naloga.')


@admin.register(BankImportRun)
class BankImportRunAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'id', 'status', 'format', 'original_filename', 'actor',
        'transactions_created', 'transactions_skipped', 'created_at', 'finished_at',
    )
    list_filter = ('status', 'format')
    search_fields = ('original_filename', 'content_sha256', 'idempotency_key')
    readonly_fields = (
        'run_uuid', 'actor', 'source', 'format', 'original_filename', 'content_sha256',
        'payload', 'status', 'idempotency_key', 'celery_task_id',
        'statements_processed', 'statements_created', 'statements_updated',
        'transactions_processed', 'transactions_created', 'transactions_skipped',
        'error_count', 'warnings', 'errors',
        'created_at', 'started_at', 'finished_at',
    )

    def has_add_permission(self, request):
        return False


@admin.register(BankSyncRun)
class BankSyncRunAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'id', 'connection', 'status', 'transactions_created',
        'auto_create_payments', 'actor', 'created_at', 'finished_at',
    )
    list_filter = ('status', 'auto_create_payments')
    readonly_fields = (
        'connection', 'actor', 'status', 'transactions_created', 'transactions_skipped',
        'last_error', 'celery_task_id', 'correlation_id', 'auto_create_payments',
        'created_at', 'started_at', 'finished_at',
    )

    def has_add_permission(self, request):
        return False


@admin.register(BankStatement)
class BankStatementAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('statement_number', 'bank_account', 'statement_date', 'status', 'closing_balance')
    list_filter = ('status', 'statement_date')
    search_fields = ('statement_number', 'bank_account__account_name')
    inlines = [BankTransactionInline]
    change_form_template = 'admin/banking/bankstatement/change_form.html'
    change_list_template = 'admin/banking/bankstatement/change_list.html'

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                'import/',
                self.admin_site.admin_view(self.bulk_import_view),
                name='banking_bankstatement_bulk_import',
            ),
            path(
                '<path:object_id>/import/',
                self.admin_site.admin_view(self.import_statement_view),
                name='banking_bankstatement_import',
            ),
            path(
                '<path:object_id>/suggest-matches/',
                self.admin_site.admin_view(self.suggest_matches_view),
                name='banking_bankstatement_suggest',
            ),
        ]
        return custom + urls

    def bulk_import_view(self, request):
        tenant = getattr(request, 'tenant', None)
        if tenant is None:
            messages.error(request, 'Import zahtijeva tenant kontekst.')
            return redirect('admin:banking_bankstatement_changelist')

        if request.method == 'POST' and request.FILES.get('import_file'):
            upload = request.FILES['import_file']
            content = upload.read()
            try:
                outcome = submit_statement_import(
                    tenant=tenant,
                    actor=request.user,
                    content=content,
                    filename=upload.name,
                )
            except Exception as exc:
                messages.error(request, f'Uvoz nije pokrenut: {exc}')
                return redirect('admin:banking_bankstatement_changelist')

            run = outcome.run
            if run.status == BankImportRun.STATUS_REJECTED:
                messages.error(
                    request,
                    f'Uvoz odbijen ({run.format or "unknown"}): '
                    f'{"; ".join(run.errors) if run.errors else "nepodržan format"}.',
                )
            elif run.status in (
                BankImportRun.STATUS_QUEUED,
                BankImportRun.STATUS_RUNNING,
            ):
                messages.info(
                    request,
                    f'Uvoz bankovnog izvoda pokrenut (run #{run.pk}). '
                    f'Status: {run.get_status_display()}.',
                )
            elif run.status == BankImportRun.STATUS_SUCCEEDED:
                messages.success(
                    request,
                    (
                        f'Import završen (run #{run.pk}): '
                        f'{run.statements_created} novih izvoda, '
                        f'{run.transactions_created} novih transakcija, '
                        f'{run.transactions_skipped} preskočenih.'
                    ),
                )
            else:
                messages.error(
                    request,
                    f'Import neuspješan (run #{run.pk}): '
                    f'{"; ".join(run.errors) if run.errors else run.status}.',
                )
            for warning in run.warnings or []:
                messages.warning(request, warning)
            return redirect('admin:banking_bankstatement_changelist')

        return render(request, 'admin/banking/bankstatement/import_statement.html', {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
        })

    @staticmethod
    def _flash_import_result(request, result) -> None:
        if result.errors and not result.statements_created and not result.transactions_created:
            messages.error(request, f'Import neuspješan ({result.format}).')
        else:
            messages.success(
                request,
                (
                    f'Import završen ({result.format}): '
                    f'{result.statements_created} novih izvoda, '
                    f'{result.transactions_created} novih transakcija, '
                    f'{result.transactions_skipped} preskočenih.'
                ),
            )
        for warning in result.warnings:
            messages.warning(request, warning)
        for error in result.errors:
            messages.error(request, error)

    def import_statement_view(self, request, object_id):
        statement = self.get_object(request, object_id)
        if request.method == 'POST' and request.FILES.get('import_file'):
            upload = request.FILES['import_file']
            content = upload.read()
            try:
                if upload.name.lower().endswith('.xml'):
                    parsed = parse_camt053(content)
                else:
                    parsed = parse_bank_csv(content)
            except Exception as exc:
                messages.error(request, f'Import neuspješan: {exc}')
                return redirect('admin:banking_bankstatement_change', object_id)

            created = 0
            for tx in parsed['transactions']:
                _, was_created = BankTransaction.all_objects.get_or_create(
                    tenant=statement.tenant,
                    bank_statement=statement,
                    external_id=tx.get('external_id') or '',
                    defaults={
                        'transaction_date': tx['transaction_date'],
                        'value_date': tx.get('value_date'),
                        'amount': tx['amount'],
                        'transaction_type': tx['transaction_type'],
                        'description': tx.get('description', ''),
                        'reference': tx.get('reference', ''),
                        'counterparty_name': tx.get('counterparty_name', ''),
                        'counterparty_iban': tx.get('counterparty_iban', ''),
                    },
                )
                if was_created:
                    created += 1
            messages.success(request, f'Uvezeno {created} transakcija.')
        return redirect('admin:banking_bankstatement_change', object_id)

    def suggest_matches_view(self, request, object_id):
        statement = self.get_object(request, object_id)
        count = suggest_matches(statement.tenant)
        messages.success(request, f'Pronađeno {count} prijedloga usklađivanja.')
        return redirect('admin:banking_bankstatement_change', object_id)


@admin.register(BankTransaction)
class BankTransactionAdmin(TenantAdminMixin, admin.ModelAdmin):
    tenant_lookup = 'bank_statement__tenant'

    list_display = (
        'transaction_date', 'amount', 'transaction_type', 'description',
        'match_status', 'matched_payment', 'matched_journal_entry', 'bank_statement',
    )
    list_filter = ('match_status', 'transaction_type', 'transaction_date')
    search_fields = ('description', 'reference', 'counterparty_name', 'counterparty_iban')
    autocomplete_fields = ('matched_journal_entry',)
    readonly_fields = ('match_status', 'matched_payment')
    actions = ['create_payments_from_transactions', 'unmatch_transactions']

    def save_model(self, request, obj, form, change):
        previous_journal_id = None
        if change and obj.pk:
            previous_journal_id = (
                BankTransaction.objects.filter(pk=obj.pk)
                .values_list('matched_journal_entry_id', flat=True)
                .first()
            )
        super().save_model(request, obj, form, change)
        journal_entry = obj.matched_journal_entry
        if journal_entry and journal_entry.id != previous_journal_id:
            try:
                match_transaction_to_journal_entry(obj, journal_entry, request.user)
            except ValidationError as exc:
                messages.error(request, str(exc))
                BankTransaction.objects.filter(pk=obj.pk).update(
                    matched_journal_entry=previous_journal_id,
                    match_status='unmatched' if not previous_journal_id else 'matched',
                )

    @admin.action(description='Kreiraj plaćanje iz transakcije')
    def create_payments_from_transactions(self, request, queryset):
        created = 0
        for tx in queryset.filter(match_status='unmatched', matched_journal_entry__isnull=True):
            try:
                create_payment_from_transaction(tx, request.user)
                created += 1
            except ValidationError as exc:
                messages.error(request, f'{tx}: {exc}')
        self.message_user(request, f'Kreirano {created} plaćanja.')

    @admin.action(description='Poništi usklađenje')
    def unmatch_transactions(self, request, queryset):
        count = 0
        for tx in queryset:
            unmatch_transaction(tx, request.user)
            count += 1
        self.message_user(request, f'Poništeno usklađenje: {count} transakcija.')
