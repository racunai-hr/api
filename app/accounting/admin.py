from decimal import Decimal

from django.contrib import admin, messages
from django.contrib.contenttypes.admin import GenericTabularInline
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from accounting.services.journal_markers import (
    DOCUMENT_MARKER_PREFIX,
    DOCUMENT_TYPE_LABELS,
    MANUAL_DOCUMENT_TYPE,
    MANUAL_DOCUMENT_TYPE_LABEL,
    document_type_label,
    iter_document_types,
)
from accounting.services.posting import _next_entry_number
from accounting.services.reports import close_fiscal_period
from domains.assets.services.activation import activate_fixed_asset, can_activate
from tenants.mixins import TenantAdminMixin

from .models import (
    AccountType,
    AnalyticAccount,
    ChartOfAccounts,
    FiscalPeriod,
    FixedAsset,
    JournalEntry,
    JournalEntryLine,
    PDVSReturn,
    PostingRule,
    RRIFChartEntry,
    SubmissionEvent,
    SubmissionEventState,
    VATLedgerEntry,
    VATLedgerOrigin,
    VATPeriod,
    VATReturn,
    VATReturnStatus,
    ZPReturn,
)
from accounting.services.submission.events import get_submission_events
from accounting.services.submission.service import AttachConfirmationError, SubmissionService


class JournalEntryLineInline(admin.TabularInline):
    model = JournalEntryLine
    extra = 2
    readonly_fields = ('line_balance_display',)

    @admin.display(description='Saldo')
    def line_balance_display(self, obj):
        if obj.pk:
            return obj.debit_amount or obj.credit_amount
        return '-'


@admin.register(FixedAsset)
class FixedAssetAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'name', 'vin', 'inventory_number', 'status', 'origin',
        'activation_readiness_display', 'acquisition_cost', 'current_book_value_display',
        'purchase_date', 'purchase_journal_entry', 'activation_journal_entry',
    )
    list_filter = ('status', 'origin', 'purchase_date')
    search_fields = ('name', 'vin', 'inventory_number', 'registration_plate')
    actions = ['activate_fixed_assets']
    list_select_related = (
        'purchase_journal_entry',
        'activation_journal_entry',
        'construction_account',
        'asset_account',
        'accumulated_depreciation_account',
        'depreciation_expense_account',
    )
    readonly_fields = (
        'accumulated_depreciation_display',
        'current_book_value_display',
        'activation_readiness_detail',
        'created_at',
        'updated_at',
    )
    autocomplete_fields = (
        'construction_account',
        'asset_account',
        'accumulated_depreciation_account',
        'depreciation_expense_account',
        'purchase_journal_entry',
        'activation_journal_entry',
        'disposal_journal_entry',
    )
    fieldsets = (
        ('Identitet', {
            'fields': (
                'name', 'inventory_number', 'vin', 'registration_plate',
                'status', 'origin', 'activation_readiness_detail',
            ),
        }),
        ('Nabava', {
            'fields': (
                'acquisition_cost', 'purchase_date', 'in_service_date', 'disposed_at',
            ),
        }),
        ('Amortizacija', {
            'fields': (
                'useful_life_months', 'depreciation_method', 'residual_value',
            ),
        }),
        ('Računovodstvo', {
            'fields': (
                'construction_account',
                'asset_account',
                'accumulated_depreciation_account',
                'depreciation_expense_account',
                'accumulated_depreciation_display',
                'current_book_value_display',
            ),
        }),
        ('Temeljnice', {
            'fields': (
                'purchase_journal_entry',
                'activation_journal_entry',
                'disposal_journal_entry',
            ),
        }),
        ('Meta', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    @admin.display(description='Spremnost')
    def activation_readiness_display(self, obj):
        if not obj.pk:
            return '-'
        if obj.status != 'in_preparation':
            return '—'
        ready, issues = can_activate(obj)
        if ready:
            return format_html('<span style="color: #0a0;">✔ Spremno</span>')
        return format_html(
            '<span style="color: #a00;" title="{}">✖ {}</span>',
            '; '.join(issues),
            issues[0],
        )

    @admin.display(description='Spremnost za aktivaciju')
    def activation_readiness_detail(self, obj):
        if not obj.pk:
            return '-'
        ready, issues = can_activate(obj)
        if ready:
            return format_html('<span style="color: #0a0;">✔ Spremno za aktivaciju</span>')
        items = ''.join(format_html('<li>{}</li>', issue) for issue in issues)
        return format_html('<span style="color: #a00;">✖ Nedostaje:</span><ul>{}</ul>', items)

    @admin.action(description='Aktiviraj OS')
    def activate_fixed_assets(self, request, queryset):
        activated = 0
        for asset in queryset:
            try:
                activate_fixed_asset(asset, request.user)
                activated += 1
            except ValidationError as exc:
                label = asset.name or asset.vin or asset.pk
                messages.error(request, f'{label}: {"; ".join(exc.messages)}')
        if activated:
            self.message_user(request, f'Aktivirano osnovnih sredstava: {activated}')

    @admin.display(description='Akumulirana amortizacija')
    def accumulated_depreciation_display(self, obj):
        if not obj.pk:
            return '-'
        return obj.accumulated_depreciation

    @admin.display(description='Knjigovodstvena vrijednost')
    def current_book_value_display(self, obj):
        if not obj.pk:
            return '-'
        return obj.current_book_value


@admin.register(RRIFChartEntry)
class RRIFChartEntryAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'account_class', 'level', 'is_synthetic', 'is_postable')
    list_filter = ('account_class', 'is_synthetic', 'account_type_name')
    search_fields = ('code', 'name')
    ordering = ('code',)


@admin.register(AccountType)
class AccountTypeAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('description',)


@admin.register(ChartOfAccounts)
class ChartOfAccountsAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'account_code', 'account_name', 'account_class', 'level',
        'is_synthetic', 'is_postable', 'is_rrif', 'is_active',
    )
    list_filter = ('account_type', 'account_class', 'is_synthetic', 'is_rrif', 'is_active')
    search_fields = ('account_code', 'account_name', 'rrif_code')
    readonly_fields = ('is_rrif', 'rrif_code', 'account_class', 'level', 'is_synthetic')

    def has_change_permission(self, request, obj=None):
        if obj and obj.is_rrif:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_rrif:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AnalyticAccount)
class AnalyticAccountAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'account_code', 'account_name', 'counterparty_type',
        'partner', 'expense_payer', 'is_active',
    )
    list_filter = ('counterparty_type', 'is_active')
    search_fields = (
        'account_code', 'account_name',
        'expense_payer__name', 'expense_payer__oib',
    )


@admin.register(FiscalPeriod)
class FiscalPeriodAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('year', 'month', 'status', 'closed_at', 'closed_by', 'report_links')
    list_filter = ('status', 'year')
    actions = ['close_periods']

    @admin.display(description='Izvještaji')
    def report_links(self, obj):
        tb = reverse('accounting:trial_balance_export', args=[obj.year, obj.month])
        bl = reverse('accounting:bilanca_export', args=[obj.year, obj.month])
        rdg = reverse('accounting:rdg_export', args=[obj.year, obj.month])
        jn = reverse('accounting:journal_export', args=[obj.year, obj.month])
        return format_html(
            '{} | {} | {} | {}',
            format_html('<a href="{}">Bruto bilanca</a>', tb),
            format_html('<a href="{}">Bilanca</a>', bl),
            format_html('<a href="{}">RDG</a>', rdg),
            format_html('<a href="{}">Dnevnik</a>', jn),
        )

    @admin.action(description='Zatvori razdoblje')
    def close_periods(self, request, queryset):
        for period in queryset.filter(status='open'):
            close_fiscal_period(period, request.user)
        self.message_user(request, 'Odabrana razdoblja su zatvorena.')


@admin.register(JournalEntry)
class JournalEntryAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'entry_number', 'entry_date', 'status', 'description',
        'total_debit_display', 'total_credit_display', 'balance_display', 'is_auto',
    )
    list_filter = ('status', 'entry_date', 'is_auto')
    search_fields = ('entry_number', 'description', 'reference')
    inlines = [JournalEntryLineInline]
    actions = ['post_entries', 'reverse_entries']
    readonly_fields = ('posted_by', 'posted_at', 'is_auto', 'matched_bank_transactions_display')

    @admin.display(description='Povezane bankovne transakcije')
    def matched_bank_transactions_display(self, obj):
        if not obj.pk:
            return '-'
        txs = obj.matched_bank_transactions.all()
        if not txs:
            return '-'
        return ', '.join(f'{tx.transaction_date} {tx.amount} EUR' for tx in txs)

    def save_model(self, request, obj, form, change):
        if not obj.entry_number and obj.entry_date:
            if not obj.tenant_id and getattr(request, 'tenant', None):
                obj.tenant = request.tenant
            if obj.tenant_id:
                obj.entry_number = _next_entry_number(obj.tenant, obj.entry_date)
        super().save_model(request, obj, form, change)

    @admin.display(description='Duguje')
    def total_debit_display(self, obj):
        return obj.total_debit

    @admin.display(description='Potražuje')
    def total_credit_display(self, obj):
        return obj.total_credit

    @admin.display(description='Razlika')
    def balance_display(self, obj):
        diff = obj.balance_difference
        if diff != Decimal('0'):
            return format_html('<span style="color:red;">{}</span>', diff)
        return diff

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        entry = form.instance
        if entry.balance_difference != Decimal('0') and entry.status == 'posted':
            entry.status = 'draft'
            entry.save(update_fields=['status'])
            messages.warning(request, 'Temeljnica nije uravnotežena — vraćena u nacrt.')

    @admin.action(description='Knjiži odabrane temeljnice')
    def post_entries(self, request, queryset):
        posted = 0
        for entry in queryset.filter(status='draft'):
            try:
                with transaction.atomic():
                    entry.post(request.user)
                posted += 1
            except ValidationError as exc:
                messages.error(request, f'{entry.entry_number}: {exc}')
        self.message_user(request, f'Knjiženo: {posted}')

    @admin.action(description='Storniraj odabrane temeljnice')
    def reverse_entries(self, request, queryset):
        reversed_count = 0
        for entry in queryset.filter(status='posted'):
            try:
                with transaction.atomic():
                    entry.reverse(request.user)
                reversed_count += 1
            except ValidationError as exc:
                messages.error(request, f'{entry.entry_number}: {exc}')
        self.message_user(request, f'Stornirano: {reversed_count}')


class DocumentTypeFilter(admin.SimpleListFilter):
    title = 'Tip knjiženja'
    parameter_name = 'document_type'

    def lookups(self, request, model_admin):
        return [
            *iter_document_types(),
            (MANUAL_DOCUMENT_TYPE, MANUAL_DOCUMENT_TYPE_LABEL),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        if value == MANUAL_DOCUMENT_TYPE:
            q = Q()
            for key in DOCUMENT_TYPE_LABELS:
                q |= Q(journal_entry__description__istartswith=f'{DOCUMENT_MARKER_PREFIX}{key}]')
            return queryset.exclude(q)
        return queryset.filter(
            journal_entry__description__istartswith=f'{DOCUMENT_MARKER_PREFIX}{value}]',
        )


@admin.register(JournalEntryLine)
class JournalEntryLineAdmin(TenantAdminMixin, admin.ModelAdmin):
    tenant_lookup = 'journal_entry__tenant'
    list_display = (
        'entry_number_link',
        'entry_date_display',
        'entry_status_display',
        'document_type_display',
        'account',
        'analytic_account',
        'debit_amount',
        'credit_amount',
    )
    list_display_links = ('entry_number_link',)
    list_filter = (
        DocumentTypeFilter,
        ('account', admin.RelatedOnlyFieldListFilter),
        ('analytic_account', admin.RelatedOnlyFieldListFilter),
        'journal_entry__status',
        'journal_entry__is_auto',
        ('journal_entry__entry_date', admin.DateFieldListFilter),
    )
    search_fields = (
        'journal_entry__entry_number',
        'journal_entry__description',
        'journal_entry__reference',
        'account__account_code',
        'account__account_name',
        'analytic_account__account_code',
        'analytic_account__account_name',
        'description',
    )
    list_select_related = (
        'journal_entry',
        'account',
        'analytic_account',
    )
    list_per_page = 50
    show_full_result_count = False
    ordering = (
        '-journal_entry__entry_date',
        '-journal_entry__entry_number',
        '-pk',
    )
    autocomplete_fields = ('account', 'analytic_account')

    @admin.display(description='Broj temeljnice', ordering='journal_entry__entry_number')
    def entry_number_link(self, obj):
        url = reverse('admin:accounting_journalentry_change', args=[obj.journal_entry_id])
        return format_html('<a href="{}">{}</a>', url, obj.journal_entry.entry_number)

    @admin.display(description='Datum', ordering='journal_entry__entry_date')
    def entry_date_display(self, obj):
        return obj.journal_entry.entry_date

    @admin.display(description='Status', ordering='journal_entry__status')
    def entry_status_display(self, obj):
        return obj.journal_entry.get_status_display()

    @admin.display(description='Tip knjiženja')
    def document_type_display(self, obj):
        return document_type_label(obj.journal_entry.description)


@admin.register(PostingRule)
class PostingRuleAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'name', 'document_type', 'debit_account_code', 'credit_account_code',
        'amount_field', 'priority', 'is_active',
    )
    list_filter = ('document_type', 'is_active')


def _pdv_s_xml_admin_link(period_id: int) -> str:
    url = reverse('accounting:pdv_s_xml_export', args=[period_id])
    return format_html('<a href="{}">PDV-S XML</a>', url)


def _zp_xml_export_link(zp_return: ZPReturn) -> str:
    if not zp_return.xml_unsigned:
        return '-'
    return format_html('<a href="{}">ZP XML</a>', zp_return.xml_unsigned.url)


def _zp_period_xml_admin_link(period_id: int) -> str:
    url = reverse('accounting:zp_xml_export', args=[period_id])
    return format_html('<a href="{}">ZP XML</a>', url)


def _pdv_xml_export_link(vat_return: VATReturn) -> str:
    from accounting.services.tax_forms.pdv.integrity import check_vat_return_integrity

    if not vat_return.xml_unsigned:
        return '-'
    integrity = check_vat_return_integrity(vat_return)
    if integrity.status == 'OUT_OF_SYNC':
        return format_html(
            '<span style="color:#ba2121;" title="{}">PDV XML (OUT OF SYNC)</span>',
            'Nepotpisani XML ne odgovara payload.json — uskladi ili generiraj novi draft.',
        )
    return format_html('<a href="{}">PDV XML</a>', vat_return.xml_unsigned.url)


class VATReturnInline(admin.TabularInline):
    model = VATReturn
    extra = 0
    fields = ('version', 'status', 'source', 'exports_display', 'created_at')
    readonly_fields = ('version', 'status', 'source', 'exports_display', 'created_at')
    show_change_link = True

    @admin.display(description='Izvoz')
    def exports_display(self, obj):
        if not obj.pk:
            return '-'
        links = []
        if obj.xml_unsigned:
            links.append(_pdv_xml_export_link(obj))
        links.append(_pdv_s_xml_admin_link(obj.vat_period_id))
        return mark_safe(' | '.join(str(link) for link in links))

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SubmissionEventInline(GenericTabularInline):
    model = SubmissionEvent
    ct_field = 'content_type'
    ct_fk_field = 'object_id'
    extra = 0
    can_delete = False
    fields = (
        'submission_no',
        'event_uuid',
        'state',
        'destination',
        'external_identifier',
        'payload_hash',
        'submitted_at',
        'source',
        'submitted_by',
        'attachments_display',
        'submission_type_display',
    )
    readonly_fields = fields

    @admin.display(description='Tip')
    def submission_type_display(self, obj):
        return obj.submission_type

    @admin.display(description='Potvrda')
    def attachments_display(self, obj):
        if not obj.pk:
            return '-'
        if obj.confirmation_attachment:
            return format_html(
                '<a href="{}">{}</a>',
                obj.confirmation_attachment.url,
                obj.confirmation_attachment.name.rsplit('/', 1)[-1],
            )
        attach_url = reverse('admin:accounting_submissionevent_attach', args=[obj.pk])
        return format_html('<a href="{}">Priloži</a>', attach_url)

    def has_add_permission(self, request, obj=None):
        return False


class PDVSReturnInline(admin.StackedInline):
    model = PDVSReturn
    extra = 0
    max_num = 1
    can_delete = False
    fields = ('version', 'pdv_s_submission_history', 'created_at')
    readonly_fields = ('version', 'pdv_s_submission_history', 'created_at')

    @admin.display(description='Povijest predaje PDV-S')
    def pdv_s_submission_history(self, obj):
        if not obj.pk:
            return '-'
        events = get_submission_events(obj)
        if not events.exists():
            return 'Nema evidencije predaje.'
        active = SubmissionService.current_submission(obj)
        rows = []
        for event in events:
            marker = ' ← active' if active and event.pk == active.pk else ''
            type_label = 'initial' if event.submission_no == 1 else 'correction'
            attachment = ' ✓' if event.confirmation_attachment else ''
            rows.append(
                f'#{event.submission_no} {event.state} {event.destination} '
                f'{event.external_identifier} {event.submitted_at:%d.%m.%Y %H:%M} '
                f'{event.source} [{type_label}]{marker}{attachment}'
            )
        return mark_safe('<br>'.join(rows))

    def has_add_permission(self, request, obj=None):
        return False


class ZpReturnSubmissionFilter(admin.SimpleListFilter):
    title = 'Status predaje'
    parameter_name = 'submission_status'

    def lookups(self, request, model_admin):
        return [
            ('draft', 'Draft'),
            ('submitted', 'Predano'),
        ]

    def queryset(self, request, queryset):
        from django.contrib.contenttypes.models import ContentType

        value = self.value()
        if not value:
            return queryset
        zp_ct = ContentType.objects.get_for_model(ZPReturn)
        submitted_ids = SubmissionEvent.all_objects.filter(
            content_type=zp_ct,
            state=SubmissionEventState.SUBMITTED,
        ).values('object_id')
        if value == 'submitted':
            return queryset.filter(pk__in=submitted_ids)
        if value == 'draft':
            return queryset.exclude(pk__in=submitted_ids)
        return queryset


class ZPReturnInline(admin.TabularInline):
    model = ZPReturn
    extra = 0
    fields = ('version', 'submission_status_display', 'exports_display', 'created_at')
    readonly_fields = ('version', 'submission_status_display', 'exports_display', 'created_at')
    show_change_link = True

    @admin.display(description='Status')
    def submission_status_display(self, obj):
        if not obj.pk:
            return '-'
        active = SubmissionService.current_submission(obj)
        if active is None:
            return 'Draft'
        return f'Predano (#{active.submission_no})'

    @admin.display(description='Izvoz')
    def exports_display(self, obj):
        if not obj.pk:
            return '-'
        return _zp_xml_export_link(obj)

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VATPeriod)
class VATPeriodAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('year', 'month', 'status', 'submitted_at', 'vat_summary_display', 'export_link')
    list_filter = ('status', 'year')
    actions = ['generate_ledger', 'generate_draft', 'generate_zp_draft', 'export_pdv_s']
    inlines = [VATReturnInline, PDVSReturnInline, ZPReturnInline]
    change_form_template = 'admin/accounting/vatperiod/change_form.html'

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<path:object_id>/mark-submitted/',
                self.admin_site.admin_view(self.mark_submitted_view),
                name='accounting_vatperiod_mark_submitted',
            ),
            path(
                '<path:object_id>/mark-pdv-s-submitted/',
                self.admin_site.admin_view(self.mark_pdv_s_submitted_view),
                name='accounting_vatperiod_mark_pdv_s_submitted',
            ),
            path(
                '<path:object_id>/mark-zp-submitted/',
                self.admin_site.admin_view(self.mark_zp_submitted_view),
                name='accounting_vatperiod_mark_zp_submitted',
            ),
            path(
                '<path:object_id>/upload-signed/',
                self.admin_site.admin_view(self.upload_signed_view),
                name='accounting_vatperiod_upload_signed',
            ),
            path(
                '<path:object_id>/reconciliation/',
                self.admin_site.admin_view(self.reconciliation_view),
                name='accounting_vatperiod_reconciliation',
            ),
        ]
        return custom + urls

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        period = get_object_or_404(VATPeriod.all_objects, pk=object_id)
        current = period.current_return
        if current is not None and current.xml_unsigned:
            from accounting.services.tax_forms.pdv.integrity import check_vat_return_integrity

            extra_context['pdv_integrity'] = check_vat_return_integrity(current)
            extra_context['pdv_integrity_return'] = current
        latest_generated = (
            period.returns.filter(status=VATReturnStatus.GENERATED)
            .order_by('-version')
            .first()
        )
        extra_context['pdv_can_mark_submitted'] = latest_generated is not None
        extra_context['pdv_mark_submitted_return'] = latest_generated
        extra_context['pdv_s_can_mark_submitted'] = True
        from accounting.services.submission.events import get_latest_zp_return

        latest_zp = get_latest_zp_return(period)
        extra_context['zp_latest_return'] = latest_zp
        extra_context['zp_can_mark_submitted'] = (
            latest_zp is not None and SubmissionService.current_submission(latest_zp) is None
        )
        extra_context['zp_mark_submitted_return'] = latest_zp
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    @admin.display(description='PDV za uplatu')
    def vat_summary_display(self, obj):
        from accounting.services.vat import aggregate_vat_period
        summary = aggregate_vat_period(obj)
        return summary['vat_due']

    @admin.display(description='Export')
    def export_link(self, obj):
        xlsx_url = reverse('accounting:vat_export', args=[obj.pk])
        return format_html(
            '<a href="{}">PDV-S XLSX</a> | {}',
            xlsx_url,
            _pdv_s_xml_admin_link(obj.pk),
        )

    @admin.action(description='Generiraj PDV knjige')
    def generate_ledger(self, request, queryset):
        from accounting.services.tax_projection.rebuild import rebuild_vat_ledger

        for period in queryset:
            result = rebuild_vat_ledger(
                period.tenant,
                period.year,
                period.month,
                actor=request.user if request.user.is_authenticated else None,
                replace=True,
            )
            if result.ok:
                messages.success(request, result.message)
            else:
                messages.error(request, result.message)

    @admin.action(description='Generiraj draft PDV obrasca')
    def generate_draft(self, request, queryset):
        from accounting.services.tax_forms.pdv.vat_returns import create_vat_return_draft

        for period in queryset:
            try:
                vat_return = create_vat_return_draft(period)
                messages.success(
                    request,
                    f'PDV {period.month:02d}/{period.year}: draft v{vat_return.version} kreiran.',
                )
            except Exception as exc:
                messages.error(request, f'PDV {period.month:02d}/{period.year}: {exc}')

    @admin.action(description='Generiraj ZP draft')
    def generate_zp_draft(self, request, queryset):
        from accounting.services.tax_forms.zp.zp_returns import create_zp_return_draft

        for period in queryset:
            try:
                zp_return = create_zp_return_draft(period)
                messages.success(
                    request,
                    f'ZP {period.month:02d}/{period.year}: draft v{zp_return.version} kreiran.',
                )
            except Exception as exc:
                messages.error(request, f'ZP {period.month:02d}/{period.year}: {exc}')

    @admin.action(description='Export PDV-S (XLSX)')
    def export_pdv_s(self, request, queryset):
        if queryset.count() != 1:
            messages.error(request, 'Odaberi točno jedno PDV razdoblje.')
            return
        period = queryset.first()
        return HttpResponseRedirect(reverse('accounting:vat_export', args=[period.pk]))

    def mark_submitted_view(self, request, object_id):
        from uuid import UUID

        from accounting.services.tax_forms.pdv.submit import (
            MarkVatReturnSubmittedError,
            mark_vat_return_submitted,
        )

        period = get_object_or_404(VATPeriod.all_objects, pk=object_id)
        vat_return = (
            period.returns.filter(status=VATReturnStatus.GENERATED)
            .order_by('-version')
            .first()
        )
        if vat_return is None:
            messages.error(request, 'Nema generiranog PDV obrasca za označavanje predaje.')
            return redirect('admin:accounting_vatperiod_change', object_id)

        if request.method == 'POST':
            version_confirmed = request.POST.get('version_confirmed') == 'on'
            submitted_at_raw = request.POST.get('submitted_at', '').strip()
            eporezna_raw = request.POST.get('eporezna_identifier', '').strip()
            submitted_at = parse_datetime(submitted_at_raw)
            if submitted_at is None and submitted_at_raw:
                messages.error(request, 'Nevaljan format datuma predaje.')
                return redirect('admin:accounting_vatperiod_mark_submitted', object_id)
            if submitted_at is None:
                submitted_at = timezone.now()
            elif timezone.is_naive(submitted_at):
                submitted_at = timezone.make_aware(submitted_at)
            try:
                eporezna_identifier = UUID(eporezna_raw)
            except ValueError:
                messages.error(request, 'ePorezna identifikator mora biti valjani UUID.')
                return redirect('admin:accounting_vatperiod_mark_submitted', object_id)

            try:
                mark_vat_return_submitted(
                    vat_return,
                    submitted_at=submitted_at,
                    eporezna_identifier=eporezna_identifier,
                    submitted_by=request.user,
                    version_confirmed=version_confirmed,
                    submission_confirmation=request.FILES.get('submission_confirmation'),
                )
                messages.success(
                    request,
                    f'PDV obrazac v{vat_return.version} označen predanim '
                    f'(UUID {eporezna_identifier}).',
                )
                return redirect('admin:accounting_vatperiod_change', object_id)
            except MarkVatReturnSubmittedError as exc:
                messages.error(request, str(exc))
                return redirect('admin:accounting_vatperiod_mark_submitted', object_id)

        return render(request, 'admin/accounting/vatperiod/mark_submitted.html', {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'period': period,
            'vat_return': vat_return,
            'submitted_by': request.user,
        })

    def mark_pdv_s_submitted_view(self, request, object_id):
        from uuid import UUID

        from accounting.services.tax_forms.pdv_s.submit import (
            MarkPdvSSubmittedError,
            mark_pdv_s_submitted,
        )

        period = get_object_or_404(VATPeriod.all_objects, pk=object_id)

        if request.method == 'POST':
            version_confirmed = request.POST.get('version_confirmed') == 'on'
            submitted_at_raw = request.POST.get('submitted_at', '').strip()
            eporezna_raw = request.POST.get('eporezna_identifier', '').strip()
            submitted_at = parse_datetime(submitted_at_raw)
            if submitted_at is None and submitted_at_raw:
                messages.error(request, 'Nevaljan format datuma predaje.')
                return redirect('admin:accounting_vatperiod_mark_pdv_s_submitted', object_id)
            if submitted_at is None:
                submitted_at = timezone.now()
            elif timezone.is_naive(submitted_at):
                submitted_at = timezone.make_aware(submitted_at)
            try:
                eporezna_identifier = UUID(eporezna_raw)
            except ValueError:
                messages.error(request, 'ePorezna identifikator mora biti valjani UUID.')
                return redirect('admin:accounting_vatperiod_mark_pdv_s_submitted', object_id)

            try:
                event = mark_pdv_s_submitted(
                    period,
                    submitted_at=submitted_at,
                    eporezna_identifier=eporezna_identifier,
                    submitted_by=request.user,
                    version_confirmed=version_confirmed,
                    submission_confirmation=request.FILES.get('submission_confirmation'),
                )
                messages.success(
                    request,
                    f'PDV-S označen predanim #{event.submission_no} '
                    f'(UUID {event.external_identifier}).',
                )
                return redirect('admin:accounting_vatperiod_change', object_id)
            except MarkPdvSSubmittedError as exc:
                messages.error(request, str(exc))
                return redirect('admin:accounting_vatperiod_mark_pdv_s_submitted', object_id)

        return render(request, 'admin/accounting/vatperiod/mark_pdv_s_submitted.html', {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'period': period,
            'submitted_by': request.user,
        })

    def mark_zp_submitted_view(self, request, object_id):
        from uuid import UUID

        from accounting.services.submission.events import get_latest_zp_return
        from accounting.services.tax_forms.zp.submit import MarkZpSubmittedError, mark_zp_submitted

        period = get_object_or_404(VATPeriod.all_objects, pk=object_id)
        zp_return = get_latest_zp_return(period)
        if zp_return is None:
            messages.error(request, 'Nema ZP drafta za označavanje predaje.')
            return redirect('admin:accounting_vatperiod_change', object_id)

        if request.method == 'POST':
            version_confirmed = request.POST.get('version_confirmed') == 'on'
            submitted_at_raw = request.POST.get('submitted_at', '').strip()
            eporezna_raw = request.POST.get('eporezna_identifier', '').strip()
            submitted_at = parse_datetime(submitted_at_raw)
            if submitted_at is None and submitted_at_raw:
                messages.error(request, 'Nevaljan format datuma predaje.')
                return redirect('admin:accounting_vatperiod_mark_zp_submitted', object_id)
            if submitted_at is None:
                submitted_at = timezone.now()
            elif timezone.is_naive(submitted_at):
                submitted_at = timezone.make_aware(submitted_at)
            try:
                eporezna_identifier = UUID(eporezna_raw)
            except ValueError:
                messages.error(request, 'ePorezna identifikator mora biti valjani UUID.')
                return redirect('admin:accounting_vatperiod_mark_zp_submitted', object_id)

            try:
                mark_zp_submitted(
                    zp_return,
                    submitted_at=submitted_at,
                    eporezna_identifier=eporezna_identifier,
                    submitted_by=request.user,
                    version_confirmed=version_confirmed,
                    submission_confirmation=request.FILES.get('submission_confirmation'),
                )
                messages.success(
                    request,
                    f'ZP obrazac v{zp_return.version} označen predanim '
                    f'(UUID {eporezna_identifier}).',
                )
                return redirect('admin:accounting_vatperiod_change', object_id)
            except MarkZpSubmittedError as exc:
                messages.error(request, str(exc))
                return redirect('admin:accounting_vatperiod_mark_zp_submitted', object_id)

        return render(request, 'admin/accounting/vatperiod/mark_zp_submitted.html', {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'period': period,
            'zp_return': zp_return,
            'submitted_by': request.user,
        })

    def upload_signed_view(self, request, object_id):
        period = get_object_or_404(VATPeriod.all_objects, pk=object_id)
        current_draft = period.current_return
        if current_draft is None:
            messages.error(request, 'Nema generiranog drafta — prvo generiraj PDV obrazac.')
            return redirect('admin:accounting_vatperiod_change', object_id)

        if request.method == 'POST' and request.FILES.get('signed_xml'):
            from accounting.services.tax_forms.pdv.diff import PayloadMismatchError
            from accounting.services.tax_forms.pdv.import_return import import_signed_vat_return

            upload = request.FILES['signed_xml']
            try:
                vat_return = import_signed_vat_return(
                    period,
                    upload.read(),
                    vat_return=current_draft,
                )
                messages.success(
                    request,
                    f'Potpisani XML uvezen — PDV obrazac v{vat_return.version} predan.',
                )
                return redirect('admin:accounting_vatperiod_change', object_id)
            except PayloadMismatchError as exc:
                messages.error(request, exc.summary())
                return render(request, 'admin/accounting/vatperiod/upload_signed.html', {
                    **self.admin_site.each_context(request),
                    'opts': self.model._meta,
                    'period': period,
                    'vat_return': current_draft,
                    'differences': exc.differences,
                })
            except Exception as exc:
                messages.error(request, f'Upload neuspješan: {exc}')
                return redirect('admin:accounting_vatperiod_upload_signed', object_id)

        return render(request, 'admin/accounting/vatperiod/upload_signed.html', {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'period': period,
            'vat_return': current_draft,
            'differences': None,
        })

    def reconciliation_view(self, request, object_id):
        from accounting.services.tax_forms.pdv.build import build_pdv_payload
        from accounting.services.tax_forms.pdv.diff import compare_pdv_payload_fields
        from accounting.services.tax_forms.pdv.import_return import payload_from_snapshot

        period = get_object_or_404(VATPeriod.all_objects, pk=object_id)
        submitted = (
            period.returns.filter(status__in=(VATReturnStatus.SUBMITTED, VATReturnStatus.IMPORTED))
            .order_by('-version')
            .first()
        )
        if submitted is None:
            messages.error(request, 'Nema predanog ili importiranog PDV obrasca za usporedbu.')
            return redirect('admin:accounting_vatperiod_change', object_id)

        erp_payload = build_pdv_payload(period)
        imported_payload = payload_from_snapshot(submitted.payload_snapshot)
        differences = compare_pdv_payload_fields(erp_payload, imported_payload)

        return render(request, 'admin/accounting/vatperiod/reconciliation.html', {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'period': period,
            'submitted': submitted,
            'differences': differences,
            'matches': not differences,
        })


@admin.register(VATReturn)
class VATReturnAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'vat_period',
        'version',
        'status',
        'source',
        'active_submission_display',
        'mapping_version',
        'schema_version',
        'exports_display',
        'created_at',
    )
    list_filter = ('status', 'source', 'vat_period__year')
    inlines = [SubmissionEventInline]
    search_fields = ('payload_hash', 'vat_period__year')
    change_form_template = 'admin/accounting/vatreturn/change_form.html'
    readonly_fields = (
        'vat_period',
        'version',
        'status',
        'source',
        'schema_version',
        'mapping_version',
        'payload_snapshot',
        'payload_hash',
        'payload_json',
        'xml_unsigned',
        'xml_submitted',
        'xml_sha256',
        'unsigned_xml_sha256',
        'integrity_display',
        'active_submission_display',
        'prepared_by',
        'superseded_by',
        'created_at',
    )
    fieldsets = (
        (None, {
            'fields': (
                'vat_period',
                'version',
                'status',
                'source',
                'integrity_display',
                'active_submission_display',
            ),
        }),
        ('Payload i datoteke', {
            'fields': (
                'schema_version',
                'mapping_version',
                'payload_snapshot',
                'payload_hash',
                'payload_json',
                'xml_unsigned',
                'unsigned_xml_sha256',
                'xml_submitted',
                'xml_sha256',
            ),
        }),
        ('Meta', {
            'fields': (
                'prepared_by',
                'superseded_by',
                'created_at',
            ),
        }),
    )

    @admin.display(description='Aktivna predaja')
    def active_submission_display(self, obj):
        if not obj.pk:
            return '-'
        active = SubmissionService.current_submission(obj)
        if active is None:
            return '-'
        attachment = ''
        if active.confirmation_attachment:
            attachment = format_html(
                ' · <a href="{}">potvrda</a>',
                active.confirmation_attachment.url,
            )
        return format_html(
            '#{} {} ({}, {}){}',
            active.submission_no,
            active.get_state_display(),
            active.destination,
            active.external_identifier,
            attachment,
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<path:object_id>/resync-xml/',
                self.admin_site.admin_view(self.resync_xml_view),
                name='accounting_vatreturn_resync_xml',
            ),
        ]
        return custom + urls

    def has_add_permission(self, request):
        return False

    @admin.display(description='Integritet drafta')
    def integrity_display(self, obj):
        if not obj.pk or not obj.xml_unsigned:
            return '-'
        from accounting.services.tax_forms.pdv.integrity import (
            check_vat_return_integrity,
            format_integrity_differences,
        )

        integrity = check_vat_return_integrity(obj)
        rows = [
            ('Payload', '✓' if integrity.payload_file_ok else '✗'),
            ('Unsigned XML', '✓' if integrity.xml_file_ok else '✗'),
            (
                'Polja (payload↔XML)',
                'MATCH' if integrity.fields_match else 'MISMATCH',
            ),
            (
                'Bytes (SHA256)',
                'MATCH' if integrity.xml_bytes_match else 'MISMATCH',
            ),
            ('Status', integrity.status),
        ]
        parts = [
            format_html(
                '{} <span style="color:{};">{}</span>',
                label,
                '#0a7d32' if value in ('✓', 'MATCH', 'SYNC') else '#ba2121',
                value,
            )
            for label, value in rows
        ]
        if integrity.differences:
            for diff_line in format_integrity_differences(integrity.differences):
                parts.append(format_html('<span style="color:#ba2121;">{}</span>', diff_line))
        return mark_safe('<br>'.join(str(part) for part in parts))

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        vat_return = get_object_or_404(VATReturn.all_objects, pk=object_id)
        from accounting.models import VATReturnSource, VATReturnStatus
        from accounting.services.tax_forms.pdv.integrity import check_vat_return_integrity

        integrity = check_vat_return_integrity(vat_return)
        extra_context['pdv_integrity'] = integrity
        extra_context['pdv_can_resync'] = (
            integrity.status == 'OUT_OF_SYNC'
            and vat_return.status in (VATReturnStatus.GENERATED, VATReturnStatus.DRAFT)
            and vat_return.source == VATReturnSource.ERP_GENERATED
        )
        extra_context['pdv_period_id'] = vat_return.vat_period_id
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def resync_xml_view(self, request, object_id):
        from accounting.services.tax_forms.pdv.vat_returns import resync_unsigned_xml_from_payload

        vat_return = get_object_or_404(VATReturn.all_objects, pk=object_id)
        if request.method != 'POST':
            return redirect('admin:accounting_vatreturn_change', object_id)

        try:
            resync_unsigned_xml_from_payload(vat_return)
            messages.success(
                request,
                f'unsigned.xml usklađen iz payload.json (v{vat_return.version}).',
            )
        except Exception as exc:
            messages.error(request, f'Resync neuspješan: {exc}')
        return redirect('admin:accounting_vatreturn_change', object_id)

    @admin.display(description='Izvoz')
    def exports_display(self, obj):
        links = []
        if obj.xml_unsigned:
            links.append(_pdv_xml_export_link(obj))
        if obj.xml_submitted:
            links.append(format_html('<a href="{}">PDV potpisani</a>', obj.xml_submitted.url))
        links.append(_pdv_s_xml_admin_link(obj.vat_period_id))
        return mark_safe(' | '.join(str(link) for link in links))

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status in (VATReturnStatus.SUBMITTED, VATReturnStatus.IMPORTED):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(ZPReturn)
class ZPReturnAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'vat_period',
        'version',
        'submission_status_display',
        'active_submission_display',
        'mapping_version',
        'schema_version',
        'exports_display',
        'created_at',
    )
    list_filter = (ZpReturnSubmissionFilter, 'vat_period__year')
    list_select_related = ('vat_period', 'prepared_by')
    search_fields = ('payload_hash', 'vat_period__year')
    inlines = [SubmissionEventInline]
    actions = ['generate_zp_draft']
    change_form_template = 'admin/accounting/zpreturn/change_form.html'
    readonly_fields = (
        'vat_period',
        'version',
        'schema_version',
        'mapping_version',
        'payload_snapshot',
        'payload_hash',
        'payload_json',
        'xml_unsigned',
        'xml_submitted',
        'unsigned_xml_sha256',
        'submission_status_display',
        'active_submission_display',
        'prepared_by',
        'created_at',
    )
    fieldsets = (
        (None, {
            'fields': (
                'vat_period',
                'version',
                'submission_status_display',
                'active_submission_display',
            ),
        }),
        ('Payload i datoteke', {
            'fields': (
                'schema_version',
                'mapping_version',
                'payload_snapshot',
                'payload_hash',
                'payload_json',
                'xml_unsigned',
                'unsigned_xml_sha256',
                'xml_submitted',
            ),
        }),
        ('Meta', {
            'fields': (
                'prepared_by',
                'created_at',
            ),
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<path:object_id>/mark-submitted/',
                self.admin_site.admin_view(self.mark_submitted_view),
                name='accounting_zpreturn_mark_submitted',
            ),
        ]
        return custom + urls

    def has_add_permission(self, request):
        return False

    @admin.display(description='Status predaje')
    def submission_status_display(self, obj):
        if not obj.pk:
            return '-'
        active = SubmissionService.current_submission(obj)
        if active is None:
            return 'Draft'
        return f'Predano (#{active.submission_no})'

    @admin.display(description='Aktivna predaja')
    def active_submission_display(self, obj):
        if not obj.pk:
            return '-'
        active = SubmissionService.current_submission(obj)
        if active is None:
            return '-'
        attachment = ''
        if active.confirmation_attachment:
            attachment = format_html(
                ' · <a href="{}">potvrda</a>',
                active.confirmation_attachment.url,
            )
        return format_html(
            '#{} {} ({}, {}){}',
            active.submission_no,
            active.get_state_display(),
            active.destination,
            active.external_identifier,
            attachment,
        )

    @admin.display(description='Izvoz')
    def exports_display(self, obj):
        links = []
        if obj.xml_unsigned:
            links.append(_zp_xml_export_link(obj))
        if obj.xml_submitted:
            links.append(format_html('<a href="{}">ZP potpisani</a>', obj.xml_submitted.url))
        links.append(_zp_period_xml_admin_link(obj.vat_period_id))
        return mark_safe(' | '.join(str(link) for link in links)) if links else '-'

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        zp_return = get_object_or_404(ZPReturn.all_objects, pk=object_id)
        extra_context['zp_can_mark_submitted'] = (
            SubmissionService.current_submission(zp_return) is None
        )
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def mark_submitted_view(self, request, object_id):
        from uuid import UUID

        from accounting.services.tax_forms.zp.submit import MarkZpSubmittedError, mark_zp_submitted

        zp_return = get_object_or_404(ZPReturn.all_objects, pk=object_id)
        if SubmissionService.current_submission(zp_return) is not None:
            messages.error(request, 'ZP obrazac je već označen predanim.')
            return redirect('admin:accounting_zpreturn_change', object_id)

        if request.method == 'POST':
            version_confirmed = request.POST.get('version_confirmed') == 'on'
            submitted_at_raw = request.POST.get('submitted_at', '').strip()
            eporezna_raw = request.POST.get('eporezna_identifier', '').strip()
            submitted_at = parse_datetime(submitted_at_raw)
            if submitted_at is None and submitted_at_raw:
                messages.error(request, 'Nevaljan format datuma predaje.')
                return redirect('admin:accounting_zpreturn_mark_submitted', object_id)
            if submitted_at is None:
                submitted_at = timezone.now()
            elif timezone.is_naive(submitted_at):
                submitted_at = timezone.make_aware(submitted_at)
            try:
                eporezna_identifier = UUID(eporezna_raw)
            except ValueError:
                messages.error(request, 'ePorezna identifikator mora biti valjani UUID.')
                return redirect('admin:accounting_zpreturn_mark_submitted', object_id)

            try:
                mark_zp_submitted(
                    zp_return,
                    submitted_at=submitted_at,
                    eporezna_identifier=eporezna_identifier,
                    submitted_by=request.user,
                    version_confirmed=version_confirmed,
                    submission_confirmation=request.FILES.get('submission_confirmation'),
                )
                messages.success(
                    request,
                    f'ZP obrazac v{zp_return.version} označen predanim '
                    f'(UUID {eporezna_identifier}).',
                )
                return redirect('admin:accounting_zpreturn_change', object_id)
            except MarkZpSubmittedError as exc:
                messages.error(request, str(exc))
                return redirect('admin:accounting_zpreturn_mark_submitted', object_id)

        return render(request, 'admin/accounting/zpreturn/mark_submitted.html', {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'zp_return': zp_return,
            'period': zp_return.vat_period,
            'submitted_by': request.user,
        })

    @admin.action(description='Generiraj novi ZP draft')
    def generate_zp_draft(self, request, queryset):
        from accounting.services.tax_forms.zp.zp_returns import create_zp_return_draft

        period_ids = set(queryset.values_list('vat_period_id', flat=True))
        periods = VATPeriod.all_objects.filter(pk__in=period_ids)
        for period in periods:
            try:
                zp_return = create_zp_return_draft(period)
                messages.success(
                    request,
                    f'ZP {period.month:02d}/{period.year}: draft v{zp_return.version} kreiran.',
                )
            except Exception as exc:
                messages.error(request, f'ZP {period.month:02d}/{period.year}: {exc}')

    def has_delete_permission(self, request, obj=None):
        if obj is not None and SubmissionService.current_submission(obj) is not None:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(SubmissionEvent)
class SubmissionEventAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'submission_no',
        'document_type_display',
        'event_uuid',
        'state',
        'destination',
        'external_identifier',
        'payload_hash',
        'submitted_at',
        'source',
        'submitted_by',
        'attachments_display',
        'submission_type_display',
    )
    list_filter = ('state', 'destination', 'source')
    readonly_fields = (
        'tenant',
        'event_uuid',
        'document_type_display',
        'content_type',
        'object_id',
        'submission_no',
        'state',
        'destination',
        'external_identifier',
        'payload_hash',
        'submitted_at',
        'submitted_by',
        'source',
        'confirmation_attachment',
        'attachments_display',
        'supersedes_submission',
        'submission_type_display',
        'created_at',
    )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<path:object_id>/attach/',
                self.admin_site.admin_view(self.attach_confirmation_view),
                name='accounting_submissionevent_attach',
            ),
        ]
        return custom + urls

    @admin.display(description='Tip dokumenta')
    def document_type_display(self, obj):
        return obj.document_type_label

    @admin.display(description='Tip')
    def submission_type_display(self, obj):
        return obj.submission_type

    @admin.display(description='Potvrda')
    def attachments_display(self, obj):
        if not obj.pk:
            return '-'
        if obj.confirmation_attachment:
            return format_html(
                '<a href="{}">{}</a>',
                obj.confirmation_attachment.url,
                obj.confirmation_attachment.name.rsplit('/', 1)[-1],
            )
        return format_html(
            '<a href="{}">Priloži potvrdu</a>',
            reverse('admin:accounting_submissionevent_attach', args=[obj.pk]),
        )

    def attach_confirmation_view(self, request, object_id):
        event = get_object_or_404(SubmissionEvent.all_objects, pk=object_id)
        if event.confirmation_attachment:
            messages.error(request, 'Potvrda predaje već postoji.')
            return redirect('admin:accounting_submissionevent_change', object_id)

        if request.method == 'POST' and request.FILES.get('confirmation_attachment'):
            try:
                SubmissionService.attach_confirmation(
                    event,
                    request.FILES['confirmation_attachment'],
                    uploaded_by=request.user,
                )
                messages.success(request, 'Potvrda predaje uspješno priložena.')
                return redirect('admin:accounting_submissionevent_change', object_id)
            except AttachConfirmationError as exc:
                messages.error(request, str(exc))

        return render(request, 'admin/accounting/submissionevent/attach_confirmation.html', {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'event': event,
            'document': event.document,
        })

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VATLedgerEntry)
class VATLedgerEntryAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'vat_period', 'ledger_type_display', 'entry_date', 'document_number',
        'partner_name', 'base_amount', 'vat_rate', 'vat_amount', 'origin',
    )
    list_filter = ('ledger_type', 'vat_period', 'origin')
    search_fields = ('document_number', 'partner_name', 'partner_oib')

    @admin.display(description='Knjiga', ordering='ledger_type')
    def ledger_type_display(self, obj):
        return obj.get_ledger_type_display()

    def _period_writable(self, period) -> None:
        if period.status in ('closed', 'submitted'):
            raise ValidationError(
                f'PDV razdoblje {period.month:02d}/{period.year} je {period.status} — '
                'ledger se ne smije mijenjati.'
            )

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.origin != VATLedgerOrigin.MANUAL:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.origin != VATLedgerOrigin.MANUAL:
            return False
        return super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.origin != VATLedgerOrigin.MANUAL:
            return [f.name for f in self.model._meta.fields]
        return readonly

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            period = VATPeriod.all_objects.select_for_update().get(pk=obj.vat_period_id)
            self._period_writable(period)
            if change:
                locked = VATLedgerEntry.all_objects.select_for_update().get(pk=obj.pk)
                if locked.origin != VATLedgerOrigin.MANUAL:
                    raise ValidationError(
                        'Generirani ledger retci se ne mogu mijenjati kroz admin.'
                    )
            obj.origin = VATLedgerOrigin.MANUAL
            obj.is_manual = True
            super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        with transaction.atomic():
            period = VATPeriod.all_objects.select_for_update().get(pk=obj.vat_period_id)
            self._period_writable(period)
            locked = VATLedgerEntry.all_objects.select_for_update().get(pk=obj.pk)
            if locked.origin != VATLedgerOrigin.MANUAL:
                raise ValidationError(
                    'Generirani ledger retci se ne mogu brisati kroz admin.'
                )
            super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            pks = list(queryset.values_list('pk', flat=True))
            if not pks:
                return
            period_ids = (
                VATLedgerEntry.all_objects.filter(pk__in=pks)
                .order_by('vat_period_id')
                .values_list('vat_period_id', flat=True)
                .distinct()
            )
            for period in (
                VATPeriod.all_objects.select_for_update()
                .filter(pk__in=list(period_ids))
                .order_by('pk')
            ):
                self._period_writable(period)
            locked = list(
                VATLedgerEntry.all_objects.select_for_update()
                .filter(pk__in=pks)
                .order_by('pk')
            )
            if any(entry.origin != VATLedgerOrigin.MANUAL for entry in locked):
                raise ValidationError(
                    'Skup sadrži generirane ledger retke — ništa nije obrisano.'
                )
            locked_pks = [entry.pk for entry in locked]
            super().delete_queryset(
                request,
                VATLedgerEntry.all_objects.filter(pk__in=locked_pks),
            )


from . import admin_subledger  # noqa: E402, F401 — SubledgerItem admin registracija
