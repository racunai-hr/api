"""Explicit VAT tax-family accounts for journal classification (ADR-0019)."""

from __future__ import annotations

from decimal import Decimal

from accounting.services.tax_forms.pdv.mapping import (
    DOMESTIC_RC_OBVEZA_ACCOUNTS,
    DOMESTIC_RC_PRETPOREZ_ACCOUNTS,
    EU_GOODS_ASSET_ACCOUNTS,
    EU_GOODS_OBVEZA_ACCOUNTS,
    EU_GOODS_PRETPOREZ_ACCOUNTS,
    EU_SERVICES_OBVEZA_ACCOUNTS,
    EU_SERVICES_PRETPOREZ_ACCOUNTS,
    IOSS_PRETPOREZ_ACCOUNTS,
    journal_line_to_box,
    journal_line_to_viii1_box,
)

_JOURNAL_OUTPUT_ACCOUNTS = frozenset({'240010', '240011', '24001'})
_JOURNAL_INPUT_ACCOUNTS = frozenset({'1400'})

MAPPED_TAX_ACCOUNTS = (
    _JOURNAL_INPUT_ACCOUNTS
    | _JOURNAL_OUTPUT_ACCOUNTS
    | EU_GOODS_ASSET_ACCOUNTS
    | EU_GOODS_PRETPOREZ_ACCOUNTS
    | EU_GOODS_OBVEZA_ACCOUNTS
    | EU_SERVICES_PRETPOREZ_ACCOUNTS
    | EU_SERVICES_OBVEZA_ACCOUNTS
    | DOMESTIC_RC_PRETPOREZ_ACCOUNTS
    | DOMESTIC_RC_OBVEZA_ACCOUNTS
    | IOSS_PRETPOREZ_ACCOUNTS
)


def _viii1_family(account_code: str) -> bool:
    if not account_code:
        return False
    if account_code.startswith('05') or account_code == '026':
        return True
    if account_code.startswith('032'):
        return True
    if account_code.startswith('037') or account_code.startswith('039'):
        return False
    return account_code.startswith(('030', '031', '033', '034', '035', '036', '038'))


def is_tax_family_account(account_code: str | None) -> bool:
    if not account_code:
        return False
    if account_code in MAPPED_TAX_ACCOUNTS:
        return True
    return _viii1_family(account_code)


def mapped_box_for_journal_line(
    *,
    account_code: str,
    debit_amount: Decimal,
    credit_amount: Decimal,
) -> str | None:
    if account_code in EU_GOODS_ASSET_ACCOUNTS and debit_amount > 0:
        return '207'
    box = journal_line_to_box(
        account_code=account_code,
        debit_amount=debit_amount,
        credit_amount=credit_amount,
    )
    if box is not None:
        return box
    return journal_line_to_viii1_box(
        account_code=account_code,
        debit_amount=debit_amount,
        credit_amount=credit_amount,
    )
