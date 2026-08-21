"""Reporting domain — Bilanca, RDG, trial balance.

Maturity: L2
Legacy apps: accounting/services/reports.py, dashboard
"""

from domains.reporting.facade import (
    MATURITY,
    balance_sheet,
    export_bilanca_xlsx,
    export_rdg_xlsx,
    export_documents,
    get_document_detail,
    income_statement,
    list_documents,
    trial_balance,
)

__all__ = [
    'MATURITY',
    'balance_sheet',
    'export_bilanca_xlsx',
    'export_rdg_xlsx',
    'export_documents',
    'get_document_detail',
    'income_statement',
    'list_documents',
    'trial_balance',
]
