"""Finance domain — GL, posting, fiscal periods, saldakonti.

Maturity: L3
Legacy apps: accounting, payments
"""

from domains.finance.facade import (
    MATURITY,
    allocate_payment,
    create_subledger_item,
    ensure_default_posting_rules,
    get_or_create_fiscal_period,
    get_partner_aging,
    post_document,
    post_invoice_payment,
    resolve_account,
)

__all__ = [
    'MATURITY',
    'allocate_payment',
    'create_subledger_item',
    'ensure_default_posting_rules',
    'get_or_create_fiscal_period',
    'get_partner_aging',
    'post_document',
    'post_invoice_payment',
    'resolve_account',
]
