"""Tax-active document statuses — same set as tax_shadow/selection.py."""

TAX_ACTIVE_INVOICE = frozenset({'sent', 'paid', 'overdue'})
TAX_ACTIVE_EXPENSE = frozenset({'approved', 'paid'})


def invoice_tax_active(status: str) -> bool:
    return status in TAX_ACTIVE_INVOICE


def expense_tax_active(status: str) -> bool:
    return status in TAX_ACTIVE_EXPENSE
