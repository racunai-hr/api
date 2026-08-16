"""Usklađivanje bankovnih transakcija s plaćanjima i temeljnicama."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import JournalEntry, JournalEntryLine
from banking.ledger import resolve_bank_ledger_account
from banking.models import BankTransaction
from payments.models import Payment

MATCH_DATE_TOLERANCE_DAYS = 0


def _ledger_line_matches_transaction(
    line: JournalEntryLine,
    ledger_account_id: int,
    bank_tx: BankTransaction,
) -> bool:
    if line.account_id != ledger_account_id:
        return False
    if bank_tx.transaction_type == 'debit':
        return line.credit_amount == bank_tx.amount and line.debit_amount == Decimal('0')
    if bank_tx.transaction_type == 'credit':
        return line.debit_amount == bank_tx.amount and line.credit_amount == Decimal('0')
    return False


def _find_matching_ledger_line(
    journal_entry: JournalEntry,
    ledger_account_id: int,
    bank_tx: BankTransaction,
) -> JournalEntryLine | None:
    for line in journal_entry.lines.select_related('account').all():
        if _ledger_line_matches_transaction(line, ledger_account_id, bank_tx):
            return line
    return None


def _validate_journal_match(bank_tx: BankTransaction, journal_entry: JournalEntry) -> None:
    if bank_tx.tenant_id != journal_entry.tenant_id:
        raise ValidationError('Temeljnica i bankovna transakcija moraju biti istog tenant-a.')
    if journal_entry.status != 'posted':
        raise ValidationError('Samo knjižena temeljnica se može uskladiti s bankovnom transakcijom.')
    if bank_tx.matched_payment_id:
        raise ValidationError('Transakcija je već usklađena s plaćanjem.')
    if abs((journal_entry.entry_date - bank_tx.transaction_date).days) > MATCH_DATE_TOLERANCE_DAYS:
        raise ValidationError(
            f'Datum temeljnice ({journal_entry.entry_date}) mora odgovarati '
            f'datumu transakcije ({bank_tx.transaction_date}).'
        )
    ledger_account = resolve_bank_ledger_account(bank_tx.bank_statement.bank_account)
    if not _find_matching_ledger_line(journal_entry, ledger_account.id, bank_tx):
        raise ValidationError(
            f'Temeljnica nema odgovarajuću stavku na kontu {ledger_account.account_code} '
            f'za iznos {bank_tx.amount}.'
        )


def suggest_matches(tenant, *, days_tolerance: int = 3) -> int:
    updated = 0
    for bank_tx in BankTransaction.all_objects.filter(tenant=tenant, match_status='unmatched'):
        date_from = bank_tx.transaction_date - timedelta(days=days_tolerance)
        date_to = bank_tx.transaction_date + timedelta(days=days_tolerance)
        expected_type = 'incoming' if bank_tx.transaction_type == 'credit' else 'outgoing'

        payments = Payment.all_objects.filter(
            tenant=tenant,
            status='completed',
            payment_type=expected_type,
            amount=bank_tx.amount,
            payment_date__gte=date_from,
            payment_date__lte=date_to,
        )
        if bank_tx.reference:
            payments = payments.filter(reference_number__icontains=bank_tx.reference[:20])
        if bank_tx.counterparty_iban:
            payments = payments.filter(counterparty_account__icontains=bank_tx.counterparty_iban[-10:])

        payment = payments.first()
        if payment:
            bank_tx.match_status = 'suggested'
            bank_tx.matched_payment = payment
            bank_tx.save(update_fields=['match_status', 'matched_payment'])
            updated += 1
    return updated


def suggest_journal_matches(tenant, *, days_tolerance: int = 0) -> int:
    updated = 0
    for bank_tx in BankTransaction.all_objects.filter(
        tenant=tenant,
        match_status='unmatched',
        matched_journal_entry__isnull=True,
        matched_payment__isnull=True,
    ).select_related('bank_statement__bank_account'):
        date_from = bank_tx.transaction_date - timedelta(days=days_tolerance)
        date_to = bank_tx.transaction_date + timedelta(days=days_tolerance)
        ledger_account = resolve_bank_ledger_account(bank_tx.bank_statement.bank_account)

        candidates = JournalEntry.all_objects.filter(
            tenant=tenant,
            status='posted',
            entry_date__gte=date_from,
            entry_date__lte=date_to,
            matched_bank_transactions__isnull=True,
        ).prefetch_related('lines')

        for entry in candidates:
            if _find_matching_ledger_line(entry, ledger_account.id, bank_tx):
                bank_tx.match_status = 'suggested'
                bank_tx.matched_journal_entry = entry
                bank_tx.save(update_fields=['match_status', 'matched_journal_entry'])
                updated += 1
                break
    return updated


@transaction.atomic
def match_transaction(bank_tx: BankTransaction, payment: Payment) -> BankTransaction:
    if bank_tx.matched_journal_entry_id:
        raise ValidationError('Transakcija je već usklađena s temeljnicom.')
    bank_tx.matched_payment = payment
    bank_tx.match_status = 'matched'
    bank_tx.save(update_fields=['matched_payment', 'match_status'])
    bank_tx.bank_statement.update_reconciliation_status()
    _post_reconciliation_if_needed(bank_tx, payment)
    return bank_tx


@transaction.atomic
def match_transaction_to_journal_entry(
    bank_tx: BankTransaction,
    journal_entry: JournalEntry,
    user,
) -> BankTransaction:
    if (
        bank_tx.matched_journal_entry_id == journal_entry.id
        and bank_tx.match_status == 'matched'
    ):
        return bank_tx
    if bank_tx.matched_journal_entry_id and bank_tx.matched_journal_entry_id != journal_entry.id:
        raise ValidationError('Transakcija je već usklađena s drugom temeljnicom.')
    if bank_tx.matched_payment_id:
        raise ValidationError('Transakcija je već usklađena s plaćanjem.')
    _validate_journal_match(bank_tx, journal_entry)
    bank_tx.matched_journal_entry = journal_entry
    bank_tx.match_status = 'matched'
    bank_tx.save(update_fields=['matched_journal_entry', 'match_status'])
    bank_tx.bank_statement.update_reconciliation_status()
    return bank_tx


@transaction.atomic
def unmatch_transaction(bank_tx: BankTransaction, user) -> BankTransaction:
    if bank_tx.match_status == 'unmatched' and not bank_tx.matched_journal_entry_id:
        return bank_tx
    bank_tx.matched_journal_entry = None
    bank_tx.matched_payment = None
    bank_tx.match_status = 'unmatched'
    bank_tx.save(update_fields=['matched_journal_entry', 'matched_payment', 'match_status'])
    bank_tx.bank_statement.update_reconciliation_status()
    return bank_tx


def _post_reconciliation_if_needed(bank_tx: BankTransaction, payment: Payment) -> None:
    """Knjiženje nakon usklađivanja ako je plaćanje vezano uz račun."""
    if not payment.related_invoice_id:
        return
    invoice = payment.related_invoice
    if invoice.status != 'paid':
        return
    from django.contrib.auth import get_user_model

    from accounting.services.posting import post_invoice_payment

    user = get_user_model().objects.filter(is_superuser=True).first()
    if user is None:
        return
    post_invoice_payment(bank_tx.tenant, invoice, payment, user)


@transaction.atomic
def create_payment_from_transaction(bank_tx: BankTransaction, user) -> Payment:
    if bank_tx.matched_journal_entry_id:
        raise ValidationError('Transakcija je već usklađena s temeljnicom.')
    payment_type = 'incoming' if bank_tx.transaction_type == 'credit' else 'outgoing'
    payment = Payment.all_objects.create(
        tenant=bank_tx.tenant,
        payment_number=f"BTX-{bank_tx.pk:06d}",
        payment_type=payment_type,
        payment_method='bank_transfer',
        status='completed',
        amount=bank_tx.amount,
        currency=bank_tx.currency,
        bank_account=bank_tx.bank_statement.bank_account,
        payment_date=bank_tx.transaction_date,
        value_date=bank_tx.value_date,
        description=bank_tx.description,
        reference_number=bank_tx.reference,
        counterparty_name=bank_tx.counterparty_name,
        counterparty_account=bank_tx.counterparty_iban,
        created_by=user,
    )
    match_transaction(bank_tx, payment)
    return payment
