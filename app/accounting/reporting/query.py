"""Zajednički upiti za financijske izvještaje (neto i audit pogled)."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import models
from django.db.models import QuerySet

from accounting.models import JournalEntry, JournalEntryLine


class ReportMode(models.TextChoices):
    NET = 'net', 'Neto'
    AUDIT = 'audit', 'Audit'


class EntryAuditKind(models.TextChoices):
    ACTIVE = 'active', 'Aktivno'
    REVERSED = 'reversed', 'Stornirano (original)'
    STORNO = 'storno', 'Storno'


@dataclass(frozen=True)
class JournalReportItem:
    entry: JournalEntry
    audit_kind: str

    @property
    def audit_label(self) -> str:
        return EntryAuditKind(self.audit_kind).label


def entry_audit_kind(entry: JournalEntry) -> str:
    if entry.status == 'reversed':
        return EntryAuditKind.REVERSED
    if entry.reversed_entry_id:
        return EntryAuditKind.STORNO
    return EntryAuditKind.ACTIVE


def _apply_period_filters(
    qs: QuerySet,
    *,
    year: int,
    month: int | None,
    cumulative: bool,
    date_field_prefix: str,
) -> QuerySet:
    qs = qs.filter(**{f'{date_field_prefix}__year': year})
    if month is not None:
        if cumulative:
            qs = qs.filter(**{f'{date_field_prefix}__month__lte': month})
        else:
            qs = qs.filter(**{f'{date_field_prefix}__month': month})
    return qs


def reporting_lines_qs(
    tenant,
    *,
    year: int,
    month: int | None = None,
    cumulative: bool = False,
    mode: str = ReportMode.NET,
) -> QuerySet[JournalEntryLine]:
    """Stavke temeljnice za agregatne izvještaje (bruto bilanca, bilanca, RDG)."""
    qs = JournalEntryLine.objects.filter(journal_entry__tenant=tenant)
    qs = _apply_period_filters(
        qs,
        year=year,
        month=month,
        cumulative=cumulative,
        date_field_prefix='journal_entry__entry_date',
    )

    if mode == ReportMode.NET:
        return qs.filter(
            journal_entry__status='posted',
            journal_entry__reversed_entry__isnull=True,
        )
    if mode == ReportMode.AUDIT:
        return qs.filter(journal_entry__status__in=['posted', 'reversed'])

    raise ValueError(f'Nepoznat report mode: {mode}')


def reporting_entries_qs(
    tenant,
    *,
    year: int,
    month: int | None = None,
    cumulative: bool = False,
    mode: str = ReportMode.AUDIT,
) -> QuerySet[JournalEntry]:
    """Temeljnice za dnevnik i audit izvoze."""
    qs = JournalEntry.all_objects.filter(tenant=tenant)
    qs = _apply_period_filters(
        qs,
        year=year,
        month=month,
        cumulative=cumulative,
        date_field_prefix='entry_date',
    )

    if mode == ReportMode.NET:
        return qs.filter(
            status='posted',
            reversed_entry__isnull=True,
        )
    if mode == ReportMode.AUDIT:
        return qs.filter(status__in=['posted', 'reversed'])

    raise ValueError(f'Nepoznat report mode: {mode}')


def journal_report_items(
    tenant,
    year: int,
    month: int,
) -> list[JournalReportItem]:
    entries = reporting_entries_qs(
        tenant,
        year=year,
        month=month,
        cumulative=False,
        mode=ReportMode.AUDIT,
    ).prefetch_related('lines__account').order_by('entry_date', 'entry_number')

    return [
        JournalReportItem(entry=entry, audit_kind=entry_audit_kind(entry))
        for entry in entries
    ]
