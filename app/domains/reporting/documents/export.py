"""CSV/XLSX export of the filtered document list (ADR-0020 §11)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

import xlsxwriter
from django.conf import settings

from domains.reporting.documents.filters import EXPORT_COLUMNS, DocumentListFilters, as_of_date

FORMULA_PREFIXES = ('=', '+', '-', '@')


def escape_spreadsheet_cell(value) -> str:
    if value is None:
        return ''
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return f"'{text}"
    return text


def _cell(row: dict, column: str) -> str:
    if column == 'subledger_state':
        return (row.get('subledger') or {}).get('state', {}).get('value') or ''
    if column == 'open_amount':
        return (row.get('subledger') or {}).get('open_amount', {}).get('value') or ''
    if column == 'vat_lifecycle':
        return (row.get('vat') or {}).get('lifecycle', {}).get('value') or ''
    if column == 'operational_status':
        return (row.get('operational_status') or {}).get('value') or ''
    if column == 'document_status':
        return (row.get('document_status') or {}).get('value') or ''
    if column == 'controls':
        return ','.join(row.get('controls') or [])
    if column in ('net', 'vat', 'gross', 'currency'):
        return (row.get('amounts') or {}).get(column) or ''
    return row.get(column) or ''


def render_csv(rows: list[dict], as_of: datetime) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['as_of', as_of.isoformat()])
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([escape_spreadsheet_cell(_cell(row, col)) for col in EXPORT_COLUMNS])
    return buffer.getvalue().encode('utf-8-sig')


def render_xlsx(rows: list[dict], as_of: datetime) -> bytes:
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False})
    sheet = workbook.add_worksheet('documents')
    sheet.write(0, 0, 'as_of')
    sheet.write(0, 1, as_of.isoformat())
    for col, name in enumerate(EXPORT_COLUMNS):
        sheet.write(1, col, name)
    for row_idx, row in enumerate(rows, start=2):
        for col, name in enumerate(EXPORT_COLUMNS):
            sheet.write(row_idx, col, escape_spreadsheet_cell(_cell(row, name)))
    workbook.close()
    return buffer.getvalue()


class ExportLimitExceeded(Exception):
    def __init__(self, limit: int, count: int):
        self.limit = limit
        self.count = count
        super().__init__(f'Izvoz prelazi {limit} redaka ({count}).')


class DocumentExportService:
    """Internal reporting export: same filters and UNION ALL builder as the list."""

    CONTENT_TYPES = {
        'csv': 'text/csv; charset=utf-8',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    }
    FILENAMES = {
        'csv': 'documents.csv',
        'xlsx': 'documents.xlsx',
    }

    def __init__(self, tenant, filters: DocumentListFilters, *, limit: int | None = None):
        self.tenant = tenant
        self.filters = filters
        self.limit = limit if limit is not None else int(
            getattr(settings, 'DOCUMENT_EXPORT_SYNC_MAX', 10000)
        )

    def export(self, fmt: str) -> tuple[bytes, str, str]:
        if fmt not in self.CONTENT_TYPES:
            raise ValueError('format mora biti csv ili xlsx')
        from domains.reporting.documents.assemble import documents_from_keys
        from domains.reporting.documents.query import count_union, materialize_union, union_rows
        from domains.reporting.documents.relations import load_page_relations
        from domains.reporting.documents.snapshot import read_snapshot

        with read_snapshot() as as_of:
            today = as_of_date(as_of)
            union = union_rows(self.tenant, self.filters, today)
            total = count_union(union)
            if total > self.limit:
                raise ExportLimitExceeded(self.limit, total)
            keys = [(row['_direction'], row['_doc_id']) for row in materialize_union(union)]
            rel = load_page_relations(self.tenant, keys)
            rows = documents_from_keys(self.tenant, keys, rel, today, detail=False)
            if fmt == 'xlsx':
                body = render_xlsx(rows, as_of)
            else:
                body = render_csv(rows, as_of)
            return body, self.CONTENT_TYPES[fmt], self.FILENAMES[fmt]
