"""CSV/XLSX export of the filtered document list (ADR-0020 §11)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

import xlsxwriter

from domains.reporting.documents.filters import EXPORT_COLUMNS

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
