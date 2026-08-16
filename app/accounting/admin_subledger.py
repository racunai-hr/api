"""Django admin — saldakonti (SubledgerItem) i aging izvještaj."""

from __future__ import annotations

from datetime import datetime

from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.html import format_html

from accounting.models import SubledgerItem
from accounting.services.reports import (
    AGING_BUCKET_KEYS,
    AGING_BUCKET_LABELS,
    export_subledger_aging_xlsx,
    partner_aging,
    subledger_open_items,
)
from tenants.mixins import TenantAdminMixin


class OverdueListFilter(admin.SimpleListFilter):
    title = 'Dospijeće'
    parameter_name = 'overdue'

    def lookups(self, request, model_admin):
        return [
            ('yes', 'Prekoračeno'),
            ('no', 'U roku'),
        ]

    def queryset(self, request, queryset):
        today = timezone.localdate()
        value = self.value()
        if value == 'yes':
            return queryset.filter(
                due_date__lt=today,
                status__in=('open', 'partial'),
            )
        if value == 'no':
            return queryset.filter(
                Q(due_date__gte=today) | ~Q(status__in=('open', 'partial')),
            )
        return queryset


class SubledgerSourceInline(GenericTabularInline):
    """Povezane stavke saldakonta na izvoru (račun / trošak)."""

    model = SubledgerItem
    ct_field = 'source_content_type'
    ct_fk_field = 'source_object_id'
    extra = 0
    can_delete = False
    max_num = 1
    fields = (
        'direction',
        'status',
        'original_amount',
        'open_amount',
        'due_date',
        'overdue_display',
        'journal_entry_link',
    )
    readonly_fields = fields
    verbose_name = 'Stavka saldakonta'
    verbose_name_plural = 'Stavke saldakonta'

    @admin.display(description='Kašnjenje')
    def overdue_display(self, obj):
        if not obj.pk:
            return '-'
        if obj.status not in ('open', 'partial'):
            return obj.get_status_display()
        days = (timezone.localdate() - obj.due_date).days
        if days <= 0:
            return 'U roku'
        return format_html('<span style="color:#ba2121;">{} dana</span>', days)

    @admin.display(description='Temeljnica')
    def journal_entry_link(self, obj):
        if not obj.pk or not obj.journal_entry_id:
            return '-'
        url = reverse('admin:accounting_journalentry_change', args=[obj.journal_entry_id])
        return format_html('<a href="{}">{}</a>', url, obj.journal_entry.entry_number)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SubledgerItem)
class SubledgerItemAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'partner',
        'direction',
        'status',
        'source_link',
        'original_amount',
        'open_amount',
        'due_date',
        'overdue_display',
        'journal_entry_link',
    )
    list_filter = (
        'direction',
        'status',
        OverdueListFilter,
        ('partner', admin.RelatedOnlyFieldListFilter),
        ('due_date', admin.DateFieldListFilter),
    )
    search_fields = ('partner__name', 'partner__tax_number')
    list_select_related = ('partner', 'journal_entry', 'source_content_type')
    ordering = ('-due_date', '-created_at')
    change_list_template = 'admin/accounting/subledgeritem/change_list.html'
    readonly_fields = (
        'partner',
        'direction',
        'source_link',
        'journal_entry_link',
        'original_amount',
        'open_amount',
        'due_date',
        'status',
        'closed_at',
        'created_at',
    )
    fieldsets = (
        (None, {
            'fields': (
                'partner',
                'direction',
                'status',
                'source_link',
                'journal_entry_link',
            ),
        }),
        ('Iznosi', {
            'fields': ('original_amount', 'open_amount', 'due_date', 'closed_at'),
        }),
        ('Meta', {
            'fields': ('created_at',),
        }),
    )

    def get_urls(self):
        custom = [
            path(
                'aging-report/',
                self.admin_site.admin_view(self.aging_report_view),
                name='accounting_subledgeritem_aging_report',
            ),
            path(
                'aging-report/export/',
                self.admin_site.admin_view(self.aging_export_view),
                name='accounting_subledgeritem_aging_export',
            ),
        ]
        return custom + super().get_urls()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Kašnjenje', ordering='due_date')
    def overdue_display(self, obj):
        if obj.status not in ('open', 'partial'):
            return '-'
        days = (timezone.localdate() - obj.due_date).days
        if days <= 0:
            return 'U roku'
        return format_html('<span style="color:#ba2121;">{} dana</span>', days)

    @admin.display(description='Izvor')
    def source_link(self, obj):
        if not obj.pk:
            return '-'
        source = obj.source
        model = obj.source_content_type.model
        if model == 'invoice':
            url = reverse('admin:invoices_invoice_change', args=[obj.source_object_id])
            label = getattr(source, 'invoice_number', None) or f'Račun #{obj.source_object_id}'
            return format_html('<a href="{}">{}</a>', url, label)
        if model == 'expense':
            url = reverse('admin:expenses_expense_change', args=[obj.source_object_id])
            label = getattr(source, 'expense_number', None) or f'Trošak #{obj.source_object_id}'
            return format_html('<a href="{}">{}</a>', url, label)
        return f'{model}#{obj.source_object_id}'

    @admin.display(description='Temeljnica')
    def journal_entry_link(self, obj):
        if not obj.pk or not obj.journal_entry_id:
            return '-'
        url = reverse('admin:accounting_journalentry_change', args=[obj.journal_entry_id])
        return format_html('<a href="{}">{}</a>', url, obj.journal_entry.entry_number)

    def _resolve_tenant(self, request):
        return getattr(request, 'tenant', None)

    def _parse_report_options(self, request):
        direction_raw = request.GET.get('direction', 'all')
        direction = None if direction_raw == 'all' else direction_raw
        if direction not in (None, 'receivable', 'payable'):
            direction = None

        as_of = None
        as_of_raw = request.GET.get('as_of', '').strip()
        if as_of_raw:
            as_of = parse_date(as_of_raw)
            if as_of is None:
                try:
                    as_of = datetime.strptime(as_of_raw, '%Y-%m-%d').date()
                except ValueError:
                    as_of = None

        partner_id = request.GET.get('partner')
        if partner_id:
            try:
                partner_id = int(partner_id)
            except (TypeError, ValueError):
                partner_id = None

        return direction, as_of, partner_id

    def aging_report_view(self, request):
        tenant = self._resolve_tenant(request)
        if tenant is None:
            return HttpResponseForbidden('Tenant nije određen.')

        direction, as_of, partner_id = self._parse_report_options(request)
        aging = partner_aging(tenant, direction=direction, as_of_date=as_of)
        open_items = subledger_open_items(tenant, direction=direction, as_of_date=aging['as_of_date'])
        if partner_id is not None:
            open_items = [row for row in open_items if row['partner_id'] == partner_id]
            aging['partners'] = [
                row for row in aging['partners'] if row['partner_id'] == partner_id
            ]
            totals = {key: sum(row['buckets'][key] for row in aging['partners']) for key in AGING_BUCKET_KEYS}
            totals['total'] = sum(row['total'] for row in aging['partners'])
            aging['totals'] = totals

        export_url = reverse('admin:accounting_subledgeritem_aging_export')
        query = request.GET.urlencode()
        if query:
            export_url = f'{export_url}?{query}'

        bucket_columns = [
            {'key': key, 'label': AGING_BUCKET_LABELS[key]}
            for key in AGING_BUCKET_KEYS
        ]
        partner_rows = []
        for row in aging['partners']:
            partner_rows.append({
                'partner_name': row['partner_name'],
                'direction_label': row['direction_label'],
                'bucket_amounts': [row['buckets'][key] for key in AGING_BUCKET_KEYS],
                'total': row['total'],
            })
        total_row = {
            'bucket_amounts': [aging['totals'][key] for key in AGING_BUCKET_KEYS],
            'total': aging['totals']['total'],
        }
        open_item_rows = [
            {
                **item,
                'bucket_label': AGING_BUCKET_LABELS[item['aging_bucket']],
            }
            for item in open_items
        ]

        context = {
            **self.admin_site.each_context(request),
            'title': 'Saldakonti — aging izvještaj',
            'opts': self.model._meta,
            'as_of_date': aging['as_of_date'],
            'partner_rows': partner_rows,
            'total_row': total_row,
            'open_items': open_item_rows,
            'bucket_columns': bucket_columns,
            'direction': direction,
            'direction_choices': [
                ('all', 'AR + AP'),
                ('receivable', 'Potraživanja (AR)'),
                ('payable', 'Obveze (AP)'),
            ],
            'partner_id': partner_id,
            'export_url': export_url,
        }
        return render(request, 'admin/accounting/subledgeritem/aging_report.html', context)

    def aging_export_view(self, request):
        from django.http import HttpResponse

        tenant = self._resolve_tenant(request)
        if tenant is None:
            return HttpResponseForbidden('Tenant nije određen.')

        direction, as_of, _partner_id = self._parse_report_options(request)
        content = export_subledger_aging_xlsx(tenant, direction=direction, as_of_date=as_of)
        as_of_date = as_of or timezone.localdate()
        filename = f'Saldakonti_aging_{tenant.slug}_{as_of_date:%Y%m%d}.xlsx'

        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        aging_url = reverse('admin:accounting_subledgeritem_aging_report')
        partner_id = request.GET.get('partner__id__exact')
        if partner_id:
            aging_url = f'{aging_url}?partner={partner_id}'
        extra_context['aging_report_url'] = aging_url
        return super().changelist_view(request, extra_context=extra_context)
