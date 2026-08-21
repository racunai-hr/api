"""Finance GL read: paginated JournalEntry list and detail."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import DecimalField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404

from accounting.models import JournalEntry, JournalEntryLine
from domains.finance.read.dto import journal_entry_detail_dto, journal_entry_list_dto
from domains.finance.read.filters import JournalEntryListFilters
from domains.reporting.documents.snapshot import isoformat, read_snapshot

_MONEY = DecimalField(max_digits=15, decimal_places=2)
_ZERO = Value(Decimal('0.00'), output_field=_MONEY)


def _totals_annotation():
    return {
        'total_debit_sum': Coalesce(Sum('lines__debit_amount'), _ZERO),
        'total_credit_sum': Coalesce(Sum('lines__credit_amount'), _ZERO),
    }


def list_journal_entries(tenant, filters: JournalEntryListFilters) -> dict:
    with read_snapshot() as as_of:
        qs = JournalEntry.all_objects.filter(tenant=tenant)
        if filters.status:
            qs = qs.filter(status=filters.status)
        if filters.date_from:
            qs = qs.filter(entry_date__gte=filters.date_from)
        if filters.date_to:
            qs = qs.filter(entry_date__lte=filters.date_to)
        if filters.search:
            term = filters.search
            qs = qs.filter(
                Q(entry_number__icontains=term)
                | Q(description__icontains=term)
                | Q(reference__icontains=term)
            )
        total = qs.count()
        start = (filters.page - 1) * filters.page_size
        page_rows = list(
            qs.select_related('source_content_type')
            .annotate(**_totals_annotation())
            .order_by('-entry_date', '-entry_number', '-id')[start : start + filters.page_size]
        )
        return {
            'as_of': isoformat(as_of),
            'count': total,
            'page': filters.page,
            'page_size': filters.page_size,
            'results': [journal_entry_list_dto(row) for row in page_rows],
        }


def get_journal_entry(tenant, entry_id: int) -> dict:
    with read_snapshot() as as_of:
        entry = (
            JournalEntry.all_objects.filter(tenant=tenant, pk=entry_id)
            .select_related('source_content_type')
            .annotate(**_totals_annotation())
            .prefetch_related(
                Prefetch(
                    'lines',
                    queryset=JournalEntryLine.objects.select_related('account').order_by('id'),
                )
            )
            .first()
        )
        if entry is None:
            raise Http404()
        return journal_entry_detail_dto(entry, as_of=isoformat(as_of))
