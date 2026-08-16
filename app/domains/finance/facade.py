"""Finance domain facade — public API."""

from accounting.services.posting import (
    ensure_default_posting_rules,
    get_or_create_fiscal_period,
    post_document,
    post_invoice_payment,
    resolve_account,
)
from domains.finance.services.aging import get_partner_aging
from domains.finance.services.subledger import allocate_payment, create_subledger_item

MATURITY = 'L3'

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
