"""Ručne temeljnice — generički servis bez poslovne logike po tipu dokumenta."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import AnalyticAccount, ChartOfAccounts, JournalEntry, JournalEntryLine
from accounting.services.posting import (
    _next_entry_number,
    get_or_create_fiscal_period,
    resolve_account,
)
from partners.models import Partner


@dataclass
class JournalLineInput:
    account_code: str
    debit: Decimal
    credit: Decimal
    description: str | None = None
    analytic_account: AnalyticAccount | None = None
    cost_center: str | None = None
    partner: Partner | None = None
    tax_code: str | None = None


def _validate_lines(lines: list[JournalLineInput]) -> None:
    if not lines:
        raise ValidationError('Temeljnica mora imati barem jednu stavku.')
    total_debit = Decimal('0')
    total_credit = Decimal('0')
    for line in lines:
        if line.debit < 0 or line.credit < 0:
            raise ValidationError('Iznosi ne smiju biti negativni.')
        if line.debit > 0 and line.credit > 0:
            raise ValidationError('Stavka ne može imati istovremeno duguje i potražuje.')
        if line.debit == 0 and line.credit == 0:
            raise ValidationError('Stavka mora imati duguje ili potražuje.')
        total_debit += line.debit
        total_credit += line.credit
    if total_debit != total_credit:
        raise ValidationError('Temeljnica mora biti uravnotežena (duguje = potražuje).')


def _ensure_period_open(tenant, entry_date: date) -> None:
    period = get_or_create_fiscal_period(tenant, entry_date)
    if period and period.status == 'closed':
        raise ValidationError(f'Fiskalno razdoblje {period.month:02d}/{period.year} je zatvoreno.')


def _resolve_line_account(tenant, line: JournalLineInput) -> ChartOfAccounts:
    account = resolve_account(tenant, line.account_code)
    if not account.is_postable:
        raise ValidationError(f'Konto {line.account_code} nije knjiživ (sintetički).')
    return account


@transaction.atomic
def create_manual_journal_entry(
    tenant,
    user: User,
    *,
    entry_date: date,
    description: str,
    lines: list[JournalLineInput],
    reference: str = '',
    post: bool = False,
) -> JournalEntry:
    _validate_lines(lines)
    _ensure_period_open(tenant, entry_date)

    entry = JournalEntry.all_objects.create(
        tenant=tenant,
        entry_number=_next_entry_number(tenant, entry_date),
        entry_date=entry_date,
        status='draft',
        description=description,
        reference=reference,
        is_auto=False,
        fiscal_period=get_or_create_fiscal_period(tenant, entry_date),
        created_by=user,
    )

    for line in lines:
        account = _resolve_line_account(tenant, line)
        JournalEntryLine.objects.create(
            journal_entry=entry,
            account=account,
            analytic_account=line.analytic_account,
            description=line.description or '',
            debit_amount=line.debit,
            credit_amount=line.credit,
        )

    if post:
        entry.post(user)

    return entry
