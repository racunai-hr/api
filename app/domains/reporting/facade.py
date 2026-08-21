"""Reporting domain facade — public API (read-only Finance/Tax aggregates)."""

from accounting.services.reports import (
    balance_sheet,
    export_bilanca_xlsx,
    export_rdg_xlsx,
    income_statement,
    trial_balance,
)
from domains.reporting.documents.service import (
    export_documents,
    get_document_detail,
    list_documents,
)

MATURITY = 'L2'

__all__ = [
    'MATURITY',
    'balance_sheet',
    'export_bilanca_xlsx',
    'export_rdg_xlsx',
    'income_statement',
    'trial_balance',
    'list_documents',
    'get_document_detail',
    'export_documents',
]
