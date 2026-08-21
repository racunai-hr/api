from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from fiscal_gateway.models import As4DocumentLink
from fiscal_gateway.services.inbound_as4_actions import InboundExpenseError
from integrations.admin_display import expense_integration_status
from super_integration.models import SuperDocumentLink
from accounting.admin_subledger import SubledgerSourceInline
from tenants.mixins import TenantAdminMixin

from .models import (
    Expense,
    ExpenseAttachment,
    ExpenseCategory,
    ExpenseImportMetadata,
    ExpensePayer,
    ExpenseSource,
    ImportBatch,
    IncomingInvoiceImport,
    ReimbursementStatus,
    SettlementMethod,
)
from .parsers.f1_csv import F1CsvParser
from .services.import_service import import_expense_rows


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'default_account', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description')


class ExpenseAttachmentInline(admin.TabularInline):
    model = ExpenseAttachment
    extra = 1
    fields = ('file', 'original_filename', 'uploaded_by', 'created_at')
    readonly_fields = ('original_filename', 'uploaded_by', 'created_at')


class ExpenseImportMetadataInline(admin.TabularInline):
    model = ExpenseImportMetadata
    extra = 0
    can_delete = False
    fields = ('source', 'external_id', 'batch_link', 'jir', 'super_guid', 'created_at')
    readonly_fields = ('source', 'external_id', 'batch_link', 'jir', 'super_guid', 'created_at')

    @admin.display(description='Batch')
    def batch_link(self, obj):
        if not obj.batch_id:
            return '-'
        url = reverse('admin:expenses_importbatch_change', args=[obj.batch_id])
        return format_html('<a href="{}">#{}</a>', url, obj.batch_id)


@admin.register(ImportBatch)
class ImportBatchAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'id', 'source', 'filename', 'uploaded_by', 'uploaded_at',
        'dry_run', 'rows_total', 'created_count', 'duplicates_count', 'errors_count',
    )
    list_filter = ('source', 'dry_run', 'uploaded_at')
    search_fields = ('filename',)
    readonly_fields = (
        'source', 'filename', 'uploaded_by', 'uploaded_at', 'dry_run',
        'rows_total', 'created_count', 'duplicates_count', 'errors_count',
        'report_payload',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ExpensePayer)
class ExpensePayerAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'name', 'oib', 'type', 'pending_reimbursement_total', 'pending_expense_count', 'is_active',
    )
    list_filter = ('type', 'is_active')
    search_fields = ('name', 'oib')
    readonly_fields = ('pending_reimbursement_total', 'pending_expense_count')

    @admin.display(description='Otvorena obveza')
    def pending_reimbursement_total(self, obj):
        if not obj.pk:
            return '-'
        total = Expense.all_objects.filter(
            tenant=obj.tenant,
            paid_by=obj,
            reimbursement_status=ReimbursementStatus.PENDING,
            status='paid',
        ).aggregate(total=Sum('amount'))['total']
        if not total:
            return format_html('<span style="color:#666;">0,00 EUR</span>')
        return format_html('<strong>{} EUR</strong>', total)

    @admin.display(description='Troškova na čekanju')
    def pending_expense_count(self, obj):
        if not obj.pk:
            return '-'
        return Expense.all_objects.filter(
            tenant=obj.tenant,
            paid_by=obj,
            reimbursement_status=ReimbursementStatus.PENDING,
            status='paid',
        ).count()


@admin.register(Expense)
class ExpenseAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'expense_number', 'status', 'paid_by', 'settlement_method', 'reimbursement_status',
        'amount', 'currency', 'supplier', 'category', 'expense_date',
    )
    list_filter = (
        'status', 'paid_by', 'settlement_method', 'reimbursement_status',
        'source', 'payment_method', 'category', 'currency', 'expense_date',
    )
    search_fields = ('expense_number', 'description', 'supplier__name', 'receipt_number')
    date_hierarchy = 'expense_date'
    readonly_fields = ('attachment_links', 'integration_status')
    change_list_template = 'admin/expenses/expense/change_list.html'
    inlines = [ExpenseAttachmentInline, ExpenseImportMetadataInline, SubledgerSourceInline]
    actions = ['approve_inbound_action', 'reject_inbound_action']

    def save_model(self, request, obj, form, change):
        from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

        with transaction.atomic():
            if obj.status in {'approved', 'paid'} and obj.expense_date:
                lock_open_vat_period_for_source_mutation(obj.tenant, obj.expense_date)
            super().save_model(request, obj, form, change)

    fieldsets = (
        ('Osnovni podaci', {
            'fields': ('expense_number', 'status', 'source', 'category', 'supplier')
        }),
        ('Plaćanje dobavljaču', {
            'fields': ('payment_method',),
            'description': 'Fiskalni podatak — kako je račun plaćen dobavljaču (F1 K/G/T).',
        }),
        ('Podmirenje u tvrtki', {
            'fields': ('settlement_method', 'paid_by', 'reimbursement_status'),
        }),
        ('Financijski podaci', {
            'fields': ('amount', 'tax_amount', 'currency')
        }),
        ('Datumi', {
            'fields': ('expense_date', 'due_date')
        }),
        ('Dokumentacija', {
            'fields': ('receipt_number', 'description', 'notes', 'attachment_links', 'integration_status')
        }),
        ('Odobravanje', {
            'fields': ('approved_by',)
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                'import-f1/',
                self.admin_site.admin_view(self.import_f1_view),
                name='expenses_expense_import_f1',
            ),
        ]
        return custom + urls

    def import_f1_view(self, request):
        tenant = getattr(request, 'tenant', None)
        if tenant is None:
            messages.error(request, 'Import zahtijeva tenant kontekst.')
            return redirect('admin:expenses_expense_changelist')

        report = None
        payers = ExpensePayer.all_objects.filter(tenant=tenant, is_active=True).order_by('name')
        if request.method == 'POST' and request.FILES.get('import_file'):
            upload = request.FILES['import_file']
            dry_run = request.POST.get('dry_run') == '1'
            mark_paid = request.POST.get('mark_paid') == '1'
            settlement_method = request.POST.get('settlement_method', '')
            paid_by_id = request.POST.get('paid_by') or None
            paid_by = None
            if paid_by_id:
                paid_by = ExpensePayer.all_objects.filter(tenant=tenant, pk=paid_by_id).first()

            if settlement_method in (SettlementMethod.PRIVATE_CARD, SettlementMethod.PRIVATE_CASH):
                if mark_paid and not paid_by:
                    messages.error(request, 'Platitelj je obavezan za privatno podmirenje plaćenih troškova.')
                    return render(request, 'admin/expenses/expense/import_f1.html', {
                        **self.admin_site.each_context(request),
                        'opts': self.model._meta,
                        'report': report,
                        'payers': payers,
                        'settlement_methods': SettlementMethod.choices,
                    })

            content = upload.read()
            parse_result = F1CsvParser().parse(content, filename=upload.name)

            if parse_result.parse_errors:
                for err in parse_result.parse_errors:
                    messages.error(request, f'Red {err.row_number} — {err.message}')

            result = import_expense_rows(
                tenant=tenant,
                user=request.user,
                rows=parse_result.rows,
                source=ExpenseSource.F1_CSV,
                filename=upload.name,
                dry_run=dry_run,
                status='paid' if mark_paid else 'approved',
                settlement_method=settlement_method,
                paid_by=paid_by,
            )
            report = self._format_import_report(result)
            if dry_run:
                messages.info(request, 'Dry run — troškovi nisu spremljeni.')
            elif result.errors and not result.created:
                messages.error(request, 'Import neuspješan.')
            else:
                messages.success(request, report.split('\n')[0])

            for issue in result.error_details:
                messages.error(request, f'Red {issue.row_number} — {issue.message}')

        return render(request, 'admin/expenses/expense/import_f1.html', {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'report': report,
            'payers': payers,
            'settlement_methods': SettlementMethod.choices,
        })

    @staticmethod
    def _format_import_report(result) -> str:
        lines = [
            (
                f'{result.rows_total} redaka | {result.created} novih | '
                f'{result.duplicates} duplikata | {result.errors} grešaka'
            ),
        ]
        if result.duplicate_receipts:
            lines.append('')
            lines.append('Duplikati:')
            for receipt in result.duplicate_receipts:
                lines.append(f'  {receipt}')
        if result.error_details:
            lines.append('')
            lines.append('Greške:')
            for issue in result.error_details:
                lines.append(f'  Red {issue.row_number} — {issue.message}')
        return '\n'.join(lines)

    @admin.display(description='Integracija (AS4)')
    def integration_status(self, obj):
        return expense_integration_status(obj)

    @admin.display(description='Prilozi')
    def attachment_links(self, obj):
        if not obj.pk:
            return '-'
        links = []
        for attachment in obj.attachments.all():
            url = reverse('expenses:attachment_download', args=[attachment.pk])
            label = attachment.original_filename or 'PDF'
            links.append(format_html('<a href="{}" target="_blank">{}</a>', url, label))
        expense_ct = ContentType.objects.get_for_model(Expense)
        as4_link = As4DocumentLink.all_objects.filter(
            tenant=obj.tenant,
            direction=As4DocumentLink.DIRECTION_INBOUND,
            content_type=expense_ct,
            object_id=obj.pk,
        ).first()
        if as4_link and as4_link.ubl_xml:
            ubl_url = reverse('expenses:expense_as4_ubl', args=[obj.pk])
            links.append(format_html('<a href="{}" target="_blank">AS4 UBL/XML</a>', ubl_url))
        super_link = SuperDocumentLink.all_objects.filter(
            tenant=obj.tenant,
            direction=SuperDocumentLink.DIRECTION_INBOUND,
            content_type=expense_ct,
            object_id=obj.pk,
        ).exclude(pdf_path='').first()
        if super_link:
            url = reverse('expenses:expense_super_pdf', args=[obj.pk])
            links.append(format_html('<a href="{}" target="_blank">SUPER PDF (legacy)</a>', url))
        if not links:
            return '-'
        return format_html_join(format_html('<br>'), '{}', ((link,) for link in links))

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, ExpenseAttachment):
                if not instance.uploaded_by_id:
                    instance.uploaded_by = request.user
                if not instance.tenant_id and instance.expense_id:
                    instance.tenant_id = instance.expense.tenant_id
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()

    @admin.action(description='Odobri ulazni eRačun i knjiži')
    def approve_inbound_action(self, request, queryset):
        approved = 0
        for expense in queryset:
            try:
                IntegrationManager.approve_inbound_expense(expense, user=request.user)
                approved += 1
            except (IntegrationError, InboundExpenseError) as exc:
                self.message_user(request, f'{expense}: {exc}', level=messages.ERROR)
        if approved:
            self.message_user(request, f'Odobreno {approved} troškova.', level=messages.SUCCESS)

    @admin.action(description='Odbij ulazni eRačun')
    def reject_inbound_action(self, request, queryset):
        rejected = 0
        for expense in queryset:
            try:
                IntegrationManager.reject_inbound_expense(expense)
                rejected += 1
            except (IntegrationError, InboundExpenseError) as exc:
                self.message_user(request, f'{expense}: {exc}', level=messages.ERROR)
        if rejected:
            self.message_user(request, f'Odbijeno {rejected} troškova.', level=messages.SUCCESS)


@admin.register(IncomingInvoiceImport)
class IncomingInvoiceImportAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'id',
        'status',
        'original_filename',
        'partner_match',
        'duplicate_kind',
        'confirmed_expense',
        'created_at',
    )
    list_filter = ('status', 'partner_match', 'duplicate_kind')
    search_fields = ('original_filename', 'file_sha256', 'idempotency_key')
    readonly_fields = (
        'import_uuid',
        'uploaded_by',
        'original_file',
        'original_filename',
        'content_type',
        'file_sha256',
        'file_size',
        'status',
        'idempotency_key',
        'celery_task_id',
        'last_error',
        'ocr_provider',
        'ocr_model',
        'ocr_schema_version',
        'ocr_extracted_at',
        'extracted_payload',
        'warnings',
        'matched_partner',
        'partner_match',
        'partner_diff',
        'partner_candidate_id',
        'duplicate_kind',
        'duplicate_expense',
        'duplicate_detail',
        'duplicate_override',
        'confirmed_expense',
        'confirmed_by',
        'confirmed_at',
        'created_at',
        'updated_at',
        'started_at',
        'finished_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

