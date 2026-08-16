"""Pomoćne funkcije za knjigovodstveni konto bankovnog računa."""

from __future__ import annotations

from accounting.models import ChartOfAccounts
from accounting.services.posting import resolve_account
from payments.models import BankAccount


def resolve_bank_ledger_account(bank_account: BankAccount) -> ChartOfAccounts:
    if bank_account.ledger_account_id:
        return bank_account.ledger_account
    return resolve_account(bank_account.tenant, '1000')
